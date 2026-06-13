"""
Odds-over-time Parquet builder — AWS Lambda handler.

Triggered (async) by the scraper Lambda when it finishes a run. For each endpoint/prefix
it reads every timestamped JSONL snapshot in S3 and rewrites a single Parquet file holding
the full odds history in tidy long form, ready for time-series charting later.

Each Parquet has exactly three columns:
    date      — datetime (snapshot time, UTC, parsed from the object key)
    selection — string   (player / team / match-selection name)
    odds      — float

pandas/pyarrow are provided by the AWS-managed "AWSSDKPandas" Lambda layer, so they are
imported lazily inside the write path — the pure transformation code needs neither.
"""

import io
import json
import os
from datetime import datetime, timezone

import boto3

s3 = boto3.client("s3")

# (S3 prefix, record type). Type drives how each JSONL record maps to (selection, odds) rows.
#   "h2h"       — two selections per record (team1/team2 with their own odds)
#   "player"    — one row, name from record["player"]
#   "team"      — one row, name from record["team"]
#   "selection" — one row, name from record["selection"]
ENDPOINTS = [
    ("odds", "h2h"),
    ("world-cup-matches", "h2h"),
    ("brownlow", "player"),
    ("rising-star", "player"),
    ("coleman", "player"),
    ("premiership", "team"),
    ("world-cup-winner", "selection"),
    ("world-cup-golden-boot", "selection"),
    ("world-cup-golden-ball", "selection"),
]

OUTPUT_PREFIX = "parquet"


def _date_from_key(key: str) -> datetime:
    """Parse the snapshot datetime from `<prefix>/YYYY/MM/DD/HH-MM-SSZ.jsonl`."""
    parts = key.split("/")
    yyyy, mm, dd = parts[-4], parts[-3], parts[-2]
    hhmmss = parts[-1].removesuffix("Z.jsonl")
    return datetime.strptime(f"{yyyy}/{mm}/{dd} {hhmmss}", "%Y/%m/%d %H-%M-%S").replace(
        tzinfo=timezone.utc
    )


def extract_rows(record_type: str, key: str, records: list[dict]) -> list[dict]:
    """Map a snapshot's JSONL records to {date, selection, odds} rows. Pure (no I/O)."""
    date = _date_from_key(key)
    rows = []
    for rec in records:
        if record_type == "h2h":
            match = rec.get("match", "")
            for team_field, odds_field in (("team1", "team1_odds"), ("team2", "team2_odds")):
                name, odds = rec.get(team_field), rec.get(odds_field)
                if name and odds is not None:
                    rows.append({"date": date, "selection": f"{match} - {name}", "odds": odds})
        else:
            name, odds = rec.get(record_type), rec.get("odds")
            if name and odds is not None:
                rows.append({"date": date, "selection": name, "odds": odds})
    return rows


def _list_dated_keys(bucket: str, prefix: str) -> list[str]:
    """All timestamped JSONL keys under `prefix/`, excluding the rolling latest.jsonl."""
    paginator = s3.get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{prefix}/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".jsonl") and not key.endswith("latest.jsonl"):
                keys.append(key)
    return sorted(keys)


def _read_jsonl(bucket: str, key: str) -> list[dict]:
    try:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
        return [json.loads(line) for line in body.splitlines() if line.strip()]
    except Exception as e:
        print(f"Could not read s3://{bucket}/{key}: {e}")
        return []


def build_endpoint(bucket: str, prefix: str, record_type: str) -> int:
    """Rebuild `parquet/<prefix>.parquet` from all snapshots under `prefix/`. Returns row count."""
    import pandas as pd

    rows = []
    for key in _list_dated_keys(bucket, prefix):
        rows.extend(extract_rows(record_type, key, _read_jsonl(bucket, key)))

    if not rows:
        print(f"{prefix}: no rows — skipping")
        return 0

    df = pd.DataFrame(rows, columns=["date", "selection", "odds"])
    buffer = io.BytesIO()
    df.to_parquet(buffer, engine="pyarrow", index=False)

    out_key = f"{OUTPUT_PREFIX}/{prefix}.parquet"
    s3.put_object(
        Bucket=bucket,
        Key=out_key,
        Body=buffer.getvalue(),
        ContentType="application/vnd.apache.parquet",
    )
    print(f"Wrote s3://{bucket}/{out_key} ({len(rows)} rows)")
    return len(rows)


def parquet_handler(event: dict, context) -> dict:
    bucket = os.environ["RESULTS_BUCKET"]
    counts = {}
    for prefix, record_type in ENDPOINTS:
        try:
            counts[prefix] = build_endpoint(bucket, prefix, record_type)
        except Exception as e:
            print(f"{prefix}: parquet build failed: {e}")
            counts[prefix] = -1
    print(f"Parquet build summary: {counts}")
    return {"statusCode": 200, "counts": counts}
