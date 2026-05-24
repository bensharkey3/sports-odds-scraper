import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import handler


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row(event_id, team1, t1_odds, team2, t2_odds, status="OPEN"):
    return {
        "event_id": event_id,
        "match": f"{team1} v {team2}",
        "team1": team1,
        "team1_odds": t1_odds,
        "team2": team2,
        "team2_odds": t2_odds,
        "betting_status": status,
    }


def _event(event_id=1, name="Richmond v Carlton", status="OPEN"):
    return {"id": event_id, "name": name, "startTime": 1716528000, "bettingStatus": status}


def _market(t1="Richmond", t1p=1.85, t2="Carlton", t2p=2.10):
    return {
        "statusCode": "Active",
        "selections": [
            {"sort": 1, "name": t1, "price": {"winPrice": t1p}},
            {"sort": 2, "name": t2, "price": {"winPrice": t2p}},
        ],
    }


# ── parse_odds ────────────────────────────────────────────────────────────────

class TestParseOdds:
    def test_all_fields_populated(self):
        row = handler.parse_odds(_event(), _market())
        assert row["event_id"] == 1
        assert row["match"] == "Richmond v Carlton"
        assert row["team1"] == "Richmond"
        assert row["team1_odds"] == 1.85
        assert row["team2"] == "Carlton"
        assert row["team2_odds"] == 2.10
        assert row["betting_status"] == "OPEN"
        assert row["market_status"] == "Active"

    def test_selections_ordered_by_sort_key(self):
        market = {
            "statusCode": "Active",
            "selections": [
                {"sort": 2, "name": "Carlton", "price": {"winPrice": 2.10}},
                {"sort": 1, "name": "Richmond", "price": {"winPrice": 1.85}},
            ],
        }
        row = handler.parse_odds(_event(), market)
        assert row["team1"] == "Richmond"
        assert row["team2"] == "Carlton"

    def test_missing_price_returns_none(self):
        market = {
            "statusCode": "Active",
            "selections": [
                {"sort": 1, "name": "Richmond", "price": {}},
                {"sort": 2, "name": "Carlton", "price": {"winPrice": 2.10}},
            ],
        }
        row = handler.parse_odds(_event(), market)
        assert row["team1_odds"] is None
        assert row["team2_odds"] == 2.10


# ── _previous_favourite ───────────────────────────────────────────────────────

CURRENT = "odds/2026/05/24/10-00-00Z.jsonl"
PREV1 = "odds/2026/05/24/08-00-00Z.jsonl"
PREV2 = "odds/2026/05/24/06-00-00Z.jsonl"


class TestPreviousFavourite:
    def test_returns_team_with_lower_odds(self):
        with patch.object(handler, "_read_jsonl", return_value=[_row(1, "Richmond", 1.85, "Carlton", 2.10)]):
            assert handler._previous_favourite("b", 1, CURRENT, [PREV1]) == "Richmond"

    def test_returns_none_when_no_history(self):
        assert handler._previous_favourite("b", 1, CURRENT, []) is None

    def test_returns_none_when_match_absent_from_history(self):
        with patch.object(handler, "_read_jsonl", return_value=[_row(99, "GWS", 1.9, "Hawthorn", 2.0)]):
            assert handler._previous_favourite("b", 1, CURRENT, [PREV1]) is None

    def test_skips_equal_odds_and_looks_further_back(self):
        def fake_read(bucket, key):
            return [_row(1, "Richmond", 2.0, "Carlton", 2.0)] if key == PREV1 else [_row(1, "Richmond", 1.90, "Carlton", 2.10)]

        with patch.object(handler, "_read_jsonl", side_effect=fake_read):
            assert handler._previous_favourite("b", 1, CURRENT, [PREV2, PREV1]) == "Richmond"

    def test_skips_off_status_and_looks_further_back(self):
        def fake_read(bucket, key):
            return [_row(1, "Richmond", 1.85, "Carlton", 2.10, status="OFF")] if key == PREV1 else [_row(1, "Carlton", 1.80, "Richmond", 2.20)]

        with patch.object(handler, "_read_jsonl", side_effect=fake_read):
            assert handler._previous_favourite("b", 1, CURRENT, [PREV2, PREV1]) == "Carlton"

    def test_excludes_current_key_from_search(self):
        # current key must never be read — only keys strictly before it
        with patch.object(handler, "_read_jsonl") as mock_read:
            handler._previous_favourite("b", 1, CURRENT, [CURRENT])
        mock_read.assert_not_called()


# ── _check_favourite_changes ──────────────────────────────────────────────────

class TestCheckFavouriteChanges:
    def test_sends_alert_when_favourite_flips(self):
        results = [_row(1, "Richmond", 1.85, "Carlton", 2.10)]
        with patch.object(handler, "_previous_favourite", return_value="Carlton"), \
             patch.object(handler, "send_slack") as mock_slack:
            handler._check_favourite_changes("b", results, CURRENT, [])
        mock_slack.assert_called_once_with(
            "Richmond v Carlton - the favourite has changed to Richmond",
            "SLACK_FAVOURITE_PARAM_NAME",
        )

    def test_no_alert_when_favourite_unchanged(self):
        results = [_row(1, "Richmond", 1.85, "Carlton", 2.10)]
        with patch.object(handler, "_previous_favourite", return_value="Richmond"), \
             patch.object(handler, "send_slack") as mock_slack:
            handler._check_favourite_changes("b", results, CURRENT, [])
        mock_slack.assert_not_called()

    def test_no_alert_when_no_previous_data(self):
        results = [_row(1, "Richmond", 1.85, "Carlton", 2.10)]
        with patch.object(handler, "_previous_favourite", return_value=None), \
             patch.object(handler, "send_slack") as mock_slack:
            handler._check_favourite_changes("b", results, CURRENT, [])
        mock_slack.assert_not_called()

    def test_skips_off_status(self):
        results = [_row(1, "Richmond", 1.85, "Carlton", 2.10, status="OFF")]
        with patch.object(handler, "_previous_favourite") as mock_prev, \
             patch.object(handler, "send_slack") as mock_slack:
            handler._check_favourite_changes("b", results, CURRENT, [])
        mock_prev.assert_not_called()
        mock_slack.assert_not_called()

    def test_skips_equal_odds(self):
        results = [_row(1, "Richmond", 2.0, "Carlton", 2.0)]
        with patch.object(handler, "_previous_favourite") as mock_prev, \
             patch.object(handler, "send_slack") as mock_slack:
            handler._check_favourite_changes("b", results, CURRENT, [])
        mock_prev.assert_not_called()
        mock_slack.assert_not_called()

    def test_skips_null_odds(self):
        results = [_row(1, "Richmond", None, "Carlton", 2.10)]
        with patch.object(handler, "_previous_favourite") as mock_prev, \
             patch.object(handler, "send_slack") as mock_slack:
            handler._check_favourite_changes("b", results, CURRENT, [])
        mock_prev.assert_not_called()
        mock_slack.assert_not_called()

    def test_alerts_for_multiple_matches(self):
        results = [
            _row(1, "Richmond", 1.85, "Carlton", 2.10),
            _row(2, "GWS", 2.20, "Hawthorn", 1.75),
        ]
        with patch.object(handler, "_previous_favourite", side_effect=["Carlton", "GWS"]), \
             patch.object(handler, "send_slack") as mock_slack:
            handler._check_favourite_changes("b", results, CURRENT, [])
        assert mock_slack.call_count == 2
