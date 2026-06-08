"""
AFL Head-to-Head odds scraper — AWS Lambda handler.
Fetches odds from Sportsbet and writes JSONL to S3.
"""

import json
import os
import time
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import boto3
import requests

MELBOURNE_TZ = ZoneInfo("Australia/Melbourne")

BASE_URL = "https://www.sportsbet.com.au/apigw/sportsbook-sports/Sportsbook/Sports"
AFL_COMPETITION_ID = 4165
BROWNLOW_COMPETITION_ID = 6136
RISING_STAR_COMPETITION_ID = 27772
COLEMAN_COMPETITION_ID = 27930

WORLD_CUP_COMPETITION_ID = 23109
WORLD_CUP_EVENT_NAME = "World Cup 2026 Outrights"
# Soccer match-result market ("Win-Draw-Win"). Unlike AFL's two-way "HH" market,
# this has three selections (team1, Draw, team2) — the Draw is dropped during parse.
WORLD_CUP_MATCH_MARKET_SORT = "MR"
# Tournament finishes mid-July 2026 — stop scraping World Cup odds after this date.
WORLD_CUP_END_DATE = date(2026, 7, 21)
# (Sportsbet market name, S3 prefix, label used in alerts/summary)
WORLD_CUP_MARKETS = [
    ("Winner 2026", "world-cup-winner", "World Cup Winner"),
    ("Golden Boot Winner", "world-cup-golden-boot", "World Cup Golden Boot"),
    ("Golden Ball (Player of the Tournament)", "world-cup-golden-ball", "World Cup Golden Ball"),
]

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


def _melbourne_timestamp(now: datetime) -> str:
    """Format a UTC datetime as a Melbourne local timestamp to the second (DST-aware)."""
    return now.astimezone(MELBOURNE_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")


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
    fav = min(priced, key=lambda r: r["odds"])
    current_fav = fav["player"]
    prev_fav = _previous_brownlow_favourite(bucket, current_key, all_keys)
    if prev_fav is not None and current_fav != prev_fav:
        event_name = players[0].get("event_name", "Brownlow Medal")
        send_slack(f"{event_name} - the favourite has changed from {prev_fav} to {current_fav} at {fav['odds']}", "SLACK_FAVOURITE_PARAM_NAME")


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
    fav = min(priced, key=lambda r: r["odds"])
    current_fav = fav["team"]
    prev_fav = _previous_premiership_favourite(bucket, current_key, all_keys)
    if prev_fav is not None and current_fav != prev_fav:
        event_name = teams[0].get("event_name", "AFL Premiership Winner")
        send_slack(f"{event_name} - the favourite has changed from {prev_fav} to {current_fav} at {fav['odds']}", "SLACK_FAVOURITE_PARAM_NAME")


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


def get_rising_star_event() -> dict | None:
    url = f"{BASE_URL}/Competitions/{RISING_STAR_COMPETITION_ID}/Events"
    response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    for e in response.json():
        if e.get("eventSort") == "TNMT":
            return e
    return None


def get_rising_star_market(event_id: int) -> dict | None:
    url = f"{BASE_URL}/Events/{event_id}/Markets"
    response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    markets = response.json()
    return markets[0] if markets else None


def parse_rising_star_odds(event: dict, market: dict, scraped_at: str) -> list[dict]:
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


def _previous_rising_star_favourite(bucket: str, before_key: str, all_keys: list[str]) -> str | None:
    for key in reversed([k for k in all_keys if k < before_key]):
        rows = _read_jsonl(bucket, key)
        priced = [r for r in rows if r.get("odds") is not None]
        if not priced:
            continue
        return min(priced, key=lambda r: r["odds"])["player"]
    return None


def _check_rising_star_favourite_change(bucket: str, players: list[dict], current_key: str, all_keys: list[str]) -> None:
    priced = [r for r in players if r.get("odds") is not None]
    if not priced:
        return
    fav = min(priced, key=lambda r: r["odds"])
    current_fav = fav["player"]
    prev_fav = _previous_rising_star_favourite(bucket, current_key, all_keys)
    if prev_fav is not None and current_fav != prev_fav:
        event_name = players[0].get("event_name", "AFL Rising Star")
        send_slack(f"{event_name} - the favourite has changed from {prev_fav} to {current_fav} at {fav['odds']}", "SLACK_FAVOURITE_PARAM_NAME")


def _scrape_rising_star(bucket: str, now: datetime, scraped_at: str) -> int:
    print("Fetching AFL Rising Star event")
    event = get_rising_star_event()
    if event is None:
        print("No AFL Rising Star event found")
        return 0

    time.sleep(DELAY_BETWEEN_REQUESTS)
    market = get_rising_star_market(event["id"])
    if market is None:
        print(f"No market for Rising Star event {event['id']}")
        return 0

    players = parse_rising_star_odds(event, market, scraped_at)
    if not players:
        print("No Rising Star selections found")
        return 0

    jsonl_body = "\n".join(json.dumps(r) for r in players) + "\n"
    dated_key = f"rising-star/{now.strftime('%Y/%m/%d')}/{now.strftime('%H-%M-%S')}Z.jsonl"
    latest_key = "rising-star/latest.jsonl"

    for key in (dated_key, latest_key):
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=jsonl_body.encode("utf-8"),
            ContentType="application/x-ndjson",
        )
        print(f"Uploaded s3://{bucket}/{key}")

    all_keys = _list_dated_keys(bucket, prefix="rising-star/")
    _check_rising_star_favourite_change(bucket, players, dated_key, all_keys)
    return len(players)


def get_coleman_event() -> dict | None:
    url = f"{BASE_URL}/Competitions/{COLEMAN_COMPETITION_ID}/Events"
    response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    for e in response.json():
        if e.get("eventSort") == "TNMT":
            return e
    return None


def get_coleman_market(event_id: int) -> dict | None:
    url = f"{BASE_URL}/Events/{event_id}/Markets"
    response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    markets = response.json()
    return markets[0] if markets else None


def parse_coleman_odds(event: dict, market: dict, scraped_at: str) -> list[dict]:
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


def _previous_coleman_favourite(bucket: str, before_key: str, all_keys: list[str]) -> str | None:
    for key in reversed([k for k in all_keys if k < before_key]):
        rows = _read_jsonl(bucket, key)
        priced = [r for r in rows if r.get("odds") is not None]
        if not priced:
            continue
        return min(priced, key=lambda r: r["odds"])["player"]
    return None


def _check_coleman_favourite_change(bucket: str, players: list[dict], current_key: str, all_keys: list[str]) -> None:
    priced = [r for r in players if r.get("odds") is not None]
    if not priced:
        return
    fav = min(priced, key=lambda r: r["odds"])
    current_fav = fav["player"]
    prev_fav = _previous_coleman_favourite(bucket, current_key, all_keys)
    if prev_fav is not None and current_fav != prev_fav:
        event_name = players[0].get("event_name", "AFL Coleman Medal")
        send_slack(f"{event_name} - the favourite has changed from {prev_fav} to {current_fav} at {fav['odds']}", "SLACK_FAVOURITE_PARAM_NAME")


def _scrape_coleman(bucket: str, now: datetime, scraped_at: str) -> int:
    print("Fetching AFL Coleman Medal event")
    event = get_coleman_event()
    if event is None:
        print("No AFL Coleman Medal event found")
        return 0

    time.sleep(DELAY_BETWEEN_REQUESTS)
    market = get_coleman_market(event["id"])
    if market is None:
        print(f"No market for Coleman Medal event {event['id']}")
        return 0

    players = parse_coleman_odds(event, market, scraped_at)
    if not players:
        print("No Coleman Medal selections found")
        return 0

    jsonl_body = "\n".join(json.dumps(r) for r in players) + "\n"
    dated_key = f"coleman/{now.strftime('%Y/%m/%d')}/{now.strftime('%H-%M-%S')}Z.jsonl"
    latest_key = "coleman/latest.jsonl"

    for key in (dated_key, latest_key):
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=jsonl_body.encode("utf-8"),
            ContentType="application/x-ndjson",
        )
        print(f"Uploaded s3://{bucket}/{key}")

    all_keys = _list_dated_keys(bucket, prefix="coleman/")
    _check_coleman_favourite_change(bucket, players, dated_key, all_keys)
    return len(players)


def get_world_cup_event() -> dict | None:
    url = f"{BASE_URL}/Competitions/{WORLD_CUP_COMPETITION_ID}/Events"
    response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    events = response.json()
    for e in events:
        if e.get("name") == WORLD_CUP_EVENT_NAME:
            return e
    # Fallback: the outrights tournament event if the exact name changes slightly
    for e in events:
        if e.get("eventSort") == "TNMT" and "Outrights" in e.get("name", ""):
            return e
    return None


def get_world_cup_markets(event_id: int) -> list[dict]:
    url = f"{BASE_URL}/Events/{event_id}/Markets"
    response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def find_market(markets: list[dict], name: str) -> dict | None:
    for market in markets:
        if market.get("name") == name:
            return market
    return None


def parse_world_cup_odds(event: dict, market: dict, scraped_at: str) -> list[dict]:
    start_dt = datetime.fromtimestamp(
        event.get("startTime", 0), tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []
    for sel in market.get("selections", []):
        rows.append({
            "event_id": event["id"],
            "event_name": event["name"],
            "market_id": market.get("id"),
            "market_name": market.get("name", ""),
            "scraped_at": scraped_at,
            "start_time": start_dt,
            "betting_status": event.get("bettingStatus", ""),
            "selection": sel.get("name", ""),
            "odds": sel.get("price", {}).get("winPrice"),
            "market_status": market.get("statusCode", ""),
        })
    return rows


def _previous_world_cup_favourite(bucket: str, prefix: str, before_key: str, all_keys: list[str]) -> str | None:
    for key in reversed([k for k in all_keys if k < before_key]):
        rows = _read_jsonl(bucket, key)
        priced = [r for r in rows if r.get("odds") is not None]
        if not priced:
            continue
        return min(priced, key=lambda r: r["odds"])["selection"]
    return None


def _check_world_cup_favourite_change(
    bucket: str, prefix: str, rows: list[dict], current_key: str, all_keys: list[str], label: str
) -> None:
    priced = [r for r in rows if r.get("odds") is not None]
    if not priced:
        return
    fav = min(priced, key=lambda r: r["odds"])
    current_fav = fav["selection"]
    prev_fav = _previous_world_cup_favourite(bucket, prefix, current_key, all_keys)
    if prev_fav is not None and current_fav != prev_fav:
        send_slack(f"{label} - the favourite has changed from {prev_fav} to {current_fav} at {fav['odds']}", "SLACK_FAVOURITE_PARAM_NAME")


def _scrape_world_cup(bucket: str, now: datetime, scraped_at: str) -> dict[str, int]:
    counts = {prefix: 0 for _, prefix, _ in WORLD_CUP_MARKETS}

    print("Fetching World Cup outrights event")
    event = get_world_cup_event()
    if event is None:
        print("No World Cup outrights event found")
        return counts

    time.sleep(DELAY_BETWEEN_REQUESTS)
    markets = get_world_cup_markets(event["id"])
    if not markets:
        print(f"No markets for World Cup event {event['id']}")
        return counts

    for market_name, prefix, label in WORLD_CUP_MARKETS:
        market = find_market(markets, market_name)
        if market is None:
            msg = f"No '{market_name}' market found for World Cup event {event['id']} — market may have been renamed"
            print(msg)
            send_slack(msg, "SLACK_ALERTS_PARAM_NAME")
            continue

        rows = parse_world_cup_odds(event, market, scraped_at)
        if not rows:
            print(f"No selections for '{market_name}'")
            continue

        jsonl_body = "\n".join(json.dumps(r) for r in rows) + "\n"
        dated_key = f"{prefix}/{now.strftime('%Y/%m/%d')}/{now.strftime('%H-%M-%S')}Z.jsonl"
        latest_key = f"{prefix}/latest.jsonl"

        for key in (dated_key, latest_key):
            s3.put_object(
                Bucket=bucket,
                Key=key,
                Body=jsonl_body.encode("utf-8"),
                ContentType="application/x-ndjson",
            )
            print(f"Uploaded s3://{bucket}/{key}")

        all_keys = _list_dated_keys(bucket, prefix=f"{prefix}/")
        _check_world_cup_favourite_change(bucket, prefix, rows, dated_key, all_keys, label)
        counts[prefix] = len(rows)

    return counts


def _check_favourite_changes(bucket: str, results: list[dict], current_key: str, all_keys: list[str]) -> None:
    for row in results:
        if row.get("betting_status") == "OFF":
            continue
        t1_odds, t2_odds = row.get("team1_odds"), row.get("team2_odds")
        if t1_odds is None or t2_odds is None or t1_odds == t2_odds:
            continue
        current_fav = row["team1"] if t1_odds < t2_odds else row["team2"]
        current_odds = t1_odds if t1_odds < t2_odds else t2_odds
        prev_fav = _previous_favourite(bucket, row["event_id"], current_key, all_keys)
        if prev_fav is not None and current_fav != prev_fav:
            send_slack(f"{row['match']} - the favourite has changed from {prev_fav} to {current_fav} at {current_odds}", "SLACK_FAVOURITE_PARAM_NAME")


def get_world_cup_matches() -> list[dict]:
    url = f"{BASE_URL}/Competitions/{WORLD_CUP_COMPETITION_ID}/Events"
    response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return [
        e for e in response.json()
        if e.get("eventSort") == "MTCH"
        and e.get("participant1")
        and e.get("participant2")
    ]


def get_world_cup_match_market(event_id: int) -> dict | None:
    url = f"{BASE_URL}/Events/{event_id}/Markets"
    response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    for market in response.json():
        if market.get("marketSort") == WORLD_CUP_MATCH_MARKET_SORT:
            return market
    return None


def parse_world_cup_match_odds(event: dict, market: dict) -> dict:
    """Parse a soccer match-result market into the same row shape as AFL H2H.

    The "Win-Draw-Win" market has three selections; the Draw is dropped so the
    two teams map onto team1/team2 exactly like an AFL match.
    """
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

    selections = sorted(
        (s for s in market.get("selections", []) if s.get("name") != "Draw"),
        key=lambda s: s.get("sort", 0),
    )
    for i, sel in enumerate(selections[:2]):
        key = f"team{i + 1}"
        row[key] = sel.get("name", "")
        row[f"{key}_odds"] = sel.get("price", {}).get("winPrice")

    return row


def _scrape_world_cup_matches(bucket: str, now: datetime) -> int:
    print("Fetching World Cup match events")
    events = get_world_cup_matches()
    print(f"Found {len(events)} World Cup match events")

    results = []
    for wc_event in events:
        time.sleep(DELAY_BETWEEN_REQUESTS)
        try:
            market = get_world_cup_match_market(wc_event["id"])
            if market is None:
                print(f"No match-result market for event {wc_event['id']}")
                continue
            results.append(parse_world_cup_match_odds(wc_event, market))
        except requests.HTTPError as e:
            print(f"HTTP error for event {wc_event['id']}: {e}")
        except Exception as e:
            print(f"Error for event {wc_event['id']}: {e}")

    if not results:
        print("No World Cup match results — nothing uploaded")
        return 0

    jsonl_body = "\n".join(json.dumps(r) for r in results) + "\n"
    dated_key = f"world-cup-matches/{now.strftime('%Y/%m/%d')}/{now.strftime('%H-%M-%S')}Z.jsonl"
    latest_key = "world-cup-matches/latest.jsonl"

    for key in (dated_key, latest_key):
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=jsonl_body.encode("utf-8"),
            ContentType="application/x-ndjson",
        )
        print(f"Uploaded s3://{bucket}/{key}")

    all_keys = _list_dated_keys(bucket, prefix="world-cup-matches/")
    _check_favourite_changes(bucket, results, dated_key, all_keys)
    return len(results)


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
    try:
        events = get_afl_events()
        print(f"Found {len(events)} match events")
    except Exception as e:
        msg = f"AFL events API failed: {e}"
        print(msg)
        send_slack(msg, "SLACK_ALERTS_PARAM_NAME")
        events = []

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
        send_slack("AFL H2H: zero records returned", "SLACK_ALERTS_PARAM_NAME")
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

    brownlow_count = 0
    try:
        brownlow_count = _scrape_brownlow(bucket, now, scraped_at)
        if brownlow_count == 0:
            send_slack("Brownlow: zero records returned", "SLACK_ALERTS_PARAM_NAME")
    except Exception as e:
        msg = f"Brownlow scrape failed: {e}"
        print(msg)
        send_slack(msg, "SLACK_ALERTS_PARAM_NAME")

    premiership_count = 0
    try:
        premiership_count = _scrape_premiership(bucket, now, scraped_at)
        if premiership_count == 0:
            send_slack("Premiership: zero records returned", "SLACK_ALERTS_PARAM_NAME")
    except Exception as e:
        msg = f"Premiership scrape failed: {e}"
        print(msg)
        send_slack(msg, "SLACK_ALERTS_PARAM_NAME")

    rising_star_count = 0
    try:
        rising_star_count = _scrape_rising_star(bucket, now, scraped_at)
        if rising_star_count == 0:
            send_slack("Rising Star: zero records returned", "SLACK_ALERTS_PARAM_NAME")
    except Exception as e:
        msg = f"Rising Star scrape failed: {e}"
        print(msg)
        send_slack(msg, "SLACK_ALERTS_PARAM_NAME")

    coleman_count = 0
    try:
        coleman_count = _scrape_coleman(bucket, now, scraped_at)
        if coleman_count == 0:
            send_slack("Coleman: zero records returned", "SLACK_ALERTS_PARAM_NAME")
    except Exception as e:
        msg = f"Coleman: scrape failed: {e}"
        print(msg)
        send_slack(msg, "SLACK_ALERTS_PARAM_NAME")

    wc_counts = {prefix: 0 for _, prefix, _ in WORLD_CUP_MARKETS}
    wc_match_count = 0
    if now.date() <= WORLD_CUP_END_DATE:
        try:
            wc_counts = _scrape_world_cup(bucket, now, scraped_at)
            for _, prefix, label in WORLD_CUP_MARKETS:
                if wc_counts[prefix] == 0:
                    send_slack(f"{label}: zero records returned", "SLACK_ALERTS_PARAM_NAME")
        except Exception as e:
            msg = f"World Cup scrape failed: {e}"
            print(msg)
            send_slack(msg, "SLACK_ALERTS_PARAM_NAME")

        try:
            wc_match_count = _scrape_world_cup_matches(bucket, now)
            if wc_match_count == 0:
                send_slack("World Cup matches: zero records returned", "SLACK_ALERTS_PARAM_NAME")
        except Exception as e:
            msg = f"World Cup matches scrape failed: {e}"
            print(msg)
            send_slack(msg, "SLACK_ALERTS_PARAM_NAME")

    send_slack(
        f":white_check_mark: sports odds scraped at {_melbourne_timestamp(now)} — "
        f"{len(results)} AFL games, {brownlow_count} Brownlow players, "
        f"{premiership_count} Premiership teams, {rising_star_count} Rising Star players, "
        f"{coleman_count} Coleman Medal players, "
        f"{wc_counts['world-cup-winner']} World Cup odds, "
        f"{wc_counts['world-cup-golden-boot']} Golden Boot odds, "
        f"{wc_counts['world-cup-golden-ball']} Golden Ball odds, "
        f"{wc_match_count} World Cup matches"
    )
    return {"statusCode": 200, "games": len(results), "s3Key": dated_key}
