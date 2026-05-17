"""
AFL Head-to-Head odds scraper — AWS Lambda handler.
Fetches odds from Sportsbet and writes JSONL to S3.
"""

import json
import os
import time
from datetime import datetime, timezone

import boto3
import requests

BASE_URL = "https://www.sportsbet.com.au/apigw/sportsbook-sports/Sportsbook/Sports"
AFL_COMPETITION_ID = 4165

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-AU,en;q=0.9",
    "Referer": "https://www.sportsbet.com.au/betting/australian-rules",
    "country-code": "AU",
    "brand": "sportsbet",
}

REQUEST_TIMEOUT = 15
DELAY_BETWEEN_REQUESTS = 0.5

s3 = boto3.client("s3")


def get_afl_events() -> list[dict]:
    url = f"{BASE_URL}/Competitions/{AFL_COMPETITION_ID}/Events"
    response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return [
        e for e in response.json()
        if e.get("eventSort") == "MTCH"
        and e.get("participant1")
        and e.get("participant2")
    ]


def get_h2h_market(event_id: int) -> dict | None:
    url = f"{BASE_URL}/Events/{event_id}/Markets"
    response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    for market in response.json():
        if market.get("marketSort") == "HH":
            return market
    return None


def parse_odds(event: dict, market: dict) -> dict:
    start_dt = datetime.fromtimestamp(
        event.get("startTime", 0), tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    row = {
        "event_id": event["id"],
        "match": event["name"],
        "start_time": start_dt,
        "betting_status": event.get("bettingStatus", ""),
        "team1": "",
        "team1_odds": None,
        "team2": "",
        "team2_odds": None,
        "market_status": market.get("statusCode", ""),
    }

    selections = sorted(market.get("selections", []), key=lambda s: s.get("sort", 0))
    for i, sel in enumerate(selections[:2]):
        key = f"team{i + 1}"
        row[key] = sel.get("name", "")
        row[f"{key}_odds"] = sel.get("price", {}).get("winPrice")

    return row


def lambda_handler(event: dict, context) -> dict:
    bucket = os.environ["RESULTS_BUCKET"]
    now = datetime.now(tz=timezone.utc)
    scraped_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"Fetching AFL events at {scraped_at}")
    events = get_afl_events()
    print(f"Found {len(events)} match events")

    results = []
    for afl_event in events:
        time.sleep(DELAY_BETWEEN_REQUESTS)
        try:
            market = get_h2h_market(afl_event["id"])
            if market is None:
                print(f"No H2H market for event {afl_event['id']}")
                continue
            results.append(parse_odds(afl_event, market))
        except requests.HTTPError as e:
            print(f"HTTP error for event {afl_event['id']}: {e}")
        except Exception as e:
            print(f"Error for event {afl_event['id']}: {e}")

    if not results:
        print("No results — nothing uploaded")
        return {"statusCode": 200, "games": 0}

    # Each line is one JSON object
    jsonl_body = "\n".join(json.dumps(r) for r in results) + "\n"

    # Timestamped file for historical record
    dated_key = f"odds/{now.strftime('%Y/%m/%d')}/{now.strftime('%H-%M-%S')}Z.jsonl"
    # Overwritten each run — easy to fetch the current snapshot
    latest_key = "odds/latest.jsonl"

    for key in (dated_key, latest_key):
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=jsonl_body.encode("utf-8"),
            ContentType="application/x-ndjson",
        )
        print(f"Uploaded s3://{bucket}/{key}")

    return {"statusCode": 200, "games": len(results), "s3Key": dated_key}
