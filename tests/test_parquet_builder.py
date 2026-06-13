import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import parquet_builder


# ── _date_from_key ──────────────────────────────────────────────────────────────

class TestDateFromKey:
    def test_parses_utc_datetime(self):
        dt = parquet_builder._date_from_key("odds/2026/06/13/10-30-00Z.jsonl")
        assert dt == datetime(2026, 6, 13, 10, 30, 0, tzinfo=timezone.utc)

    def test_handles_nested_prefix(self):
        dt = parquet_builder._date_from_key("world-cup-matches/2026/07/19/22-01-05Z.jsonl")
        assert dt == datetime(2026, 7, 19, 22, 1, 5, tzinfo=timezone.utc)


# ── extract_rows ────────────────────────────────────────────────────────────────

KEY = "odds/2026/06/13/10-30-00Z.jsonl"
EXPECTED_DATE = datetime(2026, 6, 13, 10, 30, 0, tzinfo=timezone.utc)


class TestExtractRowsH2H:
    def test_two_rows_with_match_name(self):
        rec = {"match": "Richmond v Carlton", "team1": "Richmond", "team1_odds": 1.85,
               "team2": "Carlton", "team2_odds": 2.10}
        rows = parquet_builder.extract_rows("h2h", KEY, [rec])
        assert rows == [
            {"date": EXPECTED_DATE, "selection": "Richmond v Carlton - Richmond", "odds": 1.85},
            {"date": EXPECTED_DATE, "selection": "Richmond v Carlton - Carlton", "odds": 2.10},
        ]

    def test_skips_team_with_null_odds(self):
        rec = {"match": "Richmond v Carlton", "team1": "Richmond", "team1_odds": None,
               "team2": "Carlton", "team2_odds": 2.10}
        rows = parquet_builder.extract_rows("h2h", KEY, [rec])
        assert rows == [
            {"date": EXPECTED_DATE, "selection": "Richmond v Carlton - Carlton", "odds": 2.10},
        ]


class TestExtractRowsSingle:
    def test_player(self):
        rows = parquet_builder.extract_rows("player", KEY, [{"player": "Bailey Smith", "odds": 4.0}])
        assert rows == [{"date": EXPECTED_DATE, "selection": "Bailey Smith", "odds": 4.0}]

    def test_team(self):
        rows = parquet_builder.extract_rows("team", KEY, [{"team": "Fremantle", "odds": 5.5}])
        assert rows == [{"date": EXPECTED_DATE, "selection": "Fremantle", "odds": 5.5}]

    def test_selection(self):
        rows = parquet_builder.extract_rows("selection", KEY, [{"selection": "Spain", "odds": 5.5}])
        assert rows == [{"date": EXPECTED_DATE, "selection": "Spain", "odds": 5.5}]

    def test_skips_null_odds(self):
        rows = parquet_builder.extract_rows("player", KEY, [{"player": "Bailey Smith", "odds": None}])
        assert rows == []

    def test_skips_missing_name(self):
        rows = parquet_builder.extract_rows("team", KEY, [{"odds": 5.5}])
        assert rows == []

    def test_multiple_records(self):
        recs = [{"selection": "Spain", "odds": 5.5}, {"selection": "Brazil", "odds": 6.0}]
        rows = parquet_builder.extract_rows("selection", KEY, recs)
        assert len(rows) == 2
        assert {r["selection"] for r in rows} == {"Spain", "Brazil"}
