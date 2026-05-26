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
BROWNLOW_COMPETITION_ID = 6136

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
ssm = boto3.client("ssm")

_webhook_cache: dict[str, str] = {}


def _get_webhook(param_env_var: str) -> str | None:
    param_name = os.environ.get(param_env_var)
    if not param_name:
        return None
    if param_name in _webhook_cache:
        return _webhook_cache[param_name]
    try:
        resp = ssm.get_parameter(Name=param_name, WithDecryption=True)
        _webhook_cache[param_name] = resp["Parameter"]["Value"]
    except Exception as e:
        print(f"Could not fetch webhook {param_name} from SSM: {e}")
    return _webhook_cache.get(param_name)


def send_slack(message: str, param_env_var: str = "SLACK_PARAM_NAME") -> None:
    url = _get_webhook(param_env_var)
    if not url:
        return
    try:
        requests.post(url, json={"text": message}, timeout=5)
    except Exception as e:
        print(f"Failed to send Slack notification: {e}")


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


def _list_dated_keys(bucket: str, prefix: str = "odds/") -> list[str]:
    paginator = s3.get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith("latest.jsonl"):
                keys.append(key)
    return sorted(keys)


def _read_jsonl(bucket: str, key: str) -> list[dict]:
    try:
        resp = s3.get_object(Bucket=bucket, Key=key)
        body = resp["Body"].read().decode("utf-8")
        return [json.loads(line) for line in body.splitlines() if line.strip()]
    except Exception as e:
        print(f"Could not read s3://{bucket}/{key}: {e}")
        return []


def _previous_favourite(bucket: str, event_id: int, before_key: str, all_keys: list[str]) -> str | None:
    """Scan previous files newest-first until we find one with different team odds for this match."""
    for key in reversed([k for k in all_keys if k < before_key]):
        for row in _read_jsonl(bucket, key):
            if row.get("event_id") != event_id:
                continue
            if row.get("betting_status") == "OFF":
                break  # skip this file, look further back
            t1, t2 = row.get("team1_odds"), row.get("team2_odds")
            if t1 is None or t2 is None:
                return None
            if t1 != t2:
                return row["team1"] if t1 < t2 else row["team2"]
            break  # equal odds in this file — look further back
    return None


def get_brownlow_event() -> dict | None:
    url = f"{BASE_URL}/Competitions/{BROWNLOW_COMPETITION_ID}/Events"
    response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    for e in response.json():
        if e.get("eventSort") == "TNMT":
            return e
    return None


def get_brownlow_market(event_id: int) -> dict | None:
    url = f"{BASE_URL}/Events/{event_id}/Markets"
    response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    markets = response.json()
    return markets[0] if markets else None


def parse_brownlow_odds(event: dict, market: dict, scraped_at: str) -> list[dict]:
    start_dt = datetime.fromtimestamp(
        event.get("startTime", 0), tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []
    for sel in market.get("selections", []):
        rows.append({
            "event_id": event["id"],
            "event_name": event["name"],
            "scraped_at": scraped_at,
            "start_time": start_dt,
            "betting_status": event.get("bettingStatus", ""),
            "player": sel.get("name", ""),
            "odds": sel.get("price", {}).get("winPrice"),
            "market_status": market.get("statusCode", ""),
        })
    return rows


def _previous_brownlow_favourite(bucket: str, before_key: str, all_keys: list[str]) -> str | None:
    for key in reversed([k for k in all_keys if k < before_key]):
        rows = _read_jsonl(bucket, key)
        priced = [r for r in rows if r.get("odds") is not None]
        if not priced:
            continue
        return min(priced, key=lambda r: r["odds"])["player"]
    return None


def _check_brownlow_favourite_change(bucket: str, players: list[dict], current_key: str, all_keys: list[str]) -> None:
    priced = [r for r in players if r.get("odds") is not None]
    if not priced:
        return
    current_fav = min(priced, key=lambda r: r["odds"])["player"]
    prev_fav = _previous_brownlow_favourite(bucket, current_key, all_keys)
    if prev_fav is not None and current_fav != prev_fav:
        event_name = players[0].get("event_name", "Brownlow Medal")
        send_slack(f"{event_name} - the favourite has changed to {current_fav}", "SLACK_FAVOURITE_PARAM_NAME")


def _scrape_brownlow(bucket: str, now: datetime, scraped_at: str) -> int:
    print("Fetching Brownlow Medal event")
    event = get_brownlow_event()
    if event is None:
        print("No Brownlow Medal event found")
        return 0

    time.sleep(DELAY_BETWEEN_REQUESTS)
    market = get_brownlow_market(event["id"])
    if market is None:
        print(f"No market for Brownlow event {event['id']}")
        return 0

    players = parse_brownlow_odds(event, market, scraped_at)
    if not players:
        print("No Brownlow selections found")
        return 0

    jsonl_body = "\n".join(json.dumps(r) for r in players) + "\n"
    dated_key = f"brownlow/{now.strftime('%Y/%m/%d')}/{now.strftime('%H-%M-%S')}Z.jsonl"
    latest_key = "brownlow/latest.jsonl"

    for key in (dated_key, latest_key):
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=jsonl_body.encode("utf-8"),
            ContentType="application/x-ndjson",
        )
        print(f"Uploaded s3://{bucket}/{key}")

    all_keys = _list_dated_keys(bucket, prefix="brownlow/")
    _check_brownlow_favourite_change(bucket, players, dated_key, all_keys)
    return len(players)


def get_premiership_event() -> dict | None:
    url = f"{BASE_URL}/Competitions/{AFL_COMPETITION_ID}/Events"
    response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    for e in response.json():
        if e.get("eventSort") == "GRP1" and "Premiership Winner" in e.get("name", ""):
            return e
    return None


def get_premiership_market(event_id: int) -> dict | None:
    url = f"{BASE_URL}/Events/{event_id}/Markets"
    response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    markets = response.json()
    return markets[0] if markets else None


def parse_premiership_odds(event: dict, market: dict, scraped_at: str) -> list[dict]:
    start_dt = datetime.fromtimestamp(
        event.get("startTime", 0), tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []
    for sel in market.get("selections", []):
        rows.append({
            "event_id": event["id"],
            "event_name": event["name"],
            "scraped_at": scraped_at,
            "start_time": start_dt,
            "betting_status": event.get("bettingStatus", ""),
            "team": sel.get("name", ""),
            "odds": sel.get("price", {}).get("winPrice"),
            "market_status": market.get("statusCode", ""),
        })
    return rows


def _previous_premiership_favourite(bucket: str, before_key: str, all_keys: list[str]) -> str | None:
    for key in reversed([k for k in all_keys if k < before_key]):
        rows = _read_jsonl(bucket, key)
        priced = [r for r in rows if r.get("odds") is not None]
        if not priced:
            continue
        return min(priced, key=lambda r: r["odds"])["team"]
    return None


def _check_premiership_favourite_change(bucket: str, teams: list[dict], current_key: str, all_keys: list[str]) -> None:
    priced = [r for r in teams if r.get("odds") is not None]
    if not priced:
        return
    current_fav = min(priced, key=lambda r: r["odds"])["team"]
    prev_fav = _previous_premiership_favourite(bucket, current_key, all_keys)
    if prev_fav is not None and current_fav != prev_fav:
        event_name = teams[0].get("event_name", "AFL Premiership Winner")
        send_slack(f"{event_name} - the favourite has changed to {current_fav}", "SLACK_FAVOURITE_PARAM_NAME")


def _scrape_premiership(bucket: str, now: datetime, scraped_at: str) -> int:
    print("Fetching AFL Premiership Winner event")
    event = get_premiership_event()
    if event is None:
        print("No AFL Premiership Winner event found")
        return 0

    time.sleep(DELAY_BETWEEN_REQUESTS)
    market = get_premiership_market(event["id"])
    if market is None:
        print(f"No market for Premiership event {event['id']}")
        return 0

    teams = parse_premiership_odds(event, market, scraped_at)
    if not teams:
        print("No Premiership selections found")
        return 0

    jsonl_body = "\n".join(json.dumps(r) for r in teams) + "\n"
    dated_key = f"premiership/{now.strftime('%Y/%m/%d')}/{now.strftime('%H-%M-%S')}Z.jsonl"
    latest_key = "premiership/latest.jsonl"

    for key in (dated_key, latest_key):
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=jsonl_body.encode("utf-8"),
            ContentType="application/x-ndjson",
        )
        print(f"Uploaded s3://{bucket}/{key}")

    all_keys = _list_dated_keys(bucket, prefix="premiership/")
    _check_premiership_favourite_change(bucket, teams, dated_key, all_keys)
    return len(teams)


def _check_favourite_changes(bucket: str, results: list[dict], current_key: str, all_keys: list[str]) -> None:
    for row in results:
        if row.get("betting_status") == "OFF":
            continue
        t1_odds, t2_odds = row.get("team1_odds"), row.get("team2_odds")
        if t1_odds is None or t2_odds is None or t1_odds == t2_odds:
            continue
        current_fav = row["team1"] if t1_odds < t2_odds else row["team2"]
        prev_fav = _previous_favourite(bucket, row["event_id"], current_key, all_keys)
        if prev_fav is not None and current_fav != prev_fav:
            send_slack(f"{row['match']} - the favourite has changed to {current_fav}", "SLACK_FAVOURITE_PARAM_NAME")


def s3_lambda_handler(event: dict, context) -> None:
    pass


def lambda_handler(event: dict, context) -> dict:
    try:
        return _scrape(event, context)
    except Exception as e:
        print(f"AFL odds scraper failed: {e}")
        raise


def _scrape(event: dict, context) -> dict:
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

    all_keys = _list_dated_keys(bucket)
    _check_favourite_changes(bucket, results, dated_key, all_keys)
    send_slack(f":white_check_mark: AFL odds scraped: {len(results)} games at {scraped_at}")

    try:
        _scrape_brownlow(bucket, now, scraped_at)
    except Exception as e:
        print(f"Brownlow scrape failed: {e}")

    try:
        _scrape_premiership(bucket, now, scraped_at)
    except Exception as e:
        print(f"Premiership scrape failed: {e}")

    return {"statusCode": 200, "games": len(results), "s3Key": dated_key}
