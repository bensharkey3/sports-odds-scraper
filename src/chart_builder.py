"""
Odds-over-time chart builder — AWS Lambda handler.

Triggered (async) by the parquet builder when it finishes. For every
`parquet/<name>.parquet` file it renders a line chart PNG to `charts/<name>.png`:
datetime on the x-axis, odds on the y-axis, one distinctly-coloured line per selection.

This Lambda is self-contained — pandas / matplotlib / fastparquet are bundled in its own
zip (no managed layer). They are imported lazily inside the render path so the module stays
importable (e.g. in CI) without those heavy deps, keeping the pure helpers testable.
"""

import io
import os

import boto3

s3 = boto3.client("s3")

PARQUET_PREFIX = "parquet"
CHARTS_PREFIX = "charts"


def _list_parquet_keys(bucket: str) -> list[str]:
    """All `parquet/*.parquet` keys in the bucket."""
    paginator = s3.get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{PARQUET_PREFIX}/"):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".parquet"):
                keys.append(obj["Key"])
    return sorted(keys)


def _name_from_key(key: str) -> str:
    """`parquet/world-cup-winner.parquet` -> `world-cup-winner`."""
    return key.removeprefix(f"{PARQUET_PREFIX}/").removesuffix(".parquet")


def _legend_ncols(n: int) -> int:
    """Columns for the legend — keep it from running off the bottom when there are many series."""
    return max(1, (n + 29) // 30)


def build_chart(bucket: str, key: str, name: str) -> bool:
    """Render `charts/<name>.png` from a parquet file. Returns True if written."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd

    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    df = pd.read_parquet(io.BytesIO(body), engine="fastparquet")
    if df.empty:
        print(f"{name}: parquet empty — skipping")
        return False

    selections = sorted(df["selection"].unique())
    cmap = plt.get_cmap("gist_rainbow")
    n = len(selections)

    fig, ax = plt.subplots(figsize=(14, 8))
    for i, selection in enumerate(selections):
        series = df[df["selection"] == selection].sort_values("date")
        ax.plot(
            series["date"],
            series["odds"],
            label=selection,
            color=cmap(i / max(n - 1, 1)),
            linewidth=1,
            marker=".",
            markersize=3,
        )

    ax.set_xlabel("Date")
    ax.set_ylabel("Odds")
    ax.set_title(f"{name} odds over time")
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    ax.legend(
        loc="center left",
        bbox_to_anchor=(1.01, 0.5),
        fontsize="xx-small",
        ncol=_legend_ncols(n),
    )

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)

    out_key = f"{CHARTS_PREFIX}/{name}.png"
    s3.put_object(Bucket=bucket, Key=out_key, Body=buf.getvalue(), ContentType="image/png")
    print(f"Wrote s3://{bucket}/{out_key} ({n} selections)")
    return True


def chart_handler(event: dict, context) -> dict:
    bucket = os.environ["RESULTS_BUCKET"]
    results = {}
    for key in _list_parquet_keys(bucket):
        name = _name_from_key(key)
        try:
            results[name] = build_chart(bucket, key, name)
        except Exception as e:
            print(f"{name}: chart build failed: {e}")
            results[name] = False
    print(f"Chart build summary: {results}")
    return {"statusCode": 200, "results": results}
