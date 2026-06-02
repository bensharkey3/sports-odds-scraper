import os
import sys
from unittest.mock import MagicMock, patch

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


# ── Brownlow helpers ──────────────────────────────────────────────────────────

def _brownlow_event(event_id=9641792, name="2026 AFL Brownlow Medal", status="PRICED"):
    return {"id": event_id, "name": name, "startTime": 1789983000, "bettingStatus": status, "eventSort": "TNMT"}


def _brownlow_market(selections=None):
    if selections is None:
        selections = [
            {"name": "Bailey Smith", "price": {"winPrice": 4.0}},
            {"name": "Nick Daicos", "price": {"winPrice": 4.0}},
            {"name": "Marcus Bontempelli", "price": {"winPrice": 5.0}},
        ]
    return {"statusCode": "A", "selections": selections}


def _player_row(player, odds, event_id=9641792, event_name="2026 AFL Brownlow Medal"):
    return {"event_id": event_id, "event_name": event_name, "player": player, "odds": odds}


# ── parse_brownlow_odds ───────────────────────────────────────────────────────

class TestParseBrownlowOdds:
    def test_returns_one_row_per_player(self):
        rows = handler.parse_brownlow_odds(_brownlow_event(), _brownlow_market(), "2026-05-26T10:00:00Z")
        assert len(rows) == 3

    def test_row_fields(self):
        rows = handler.parse_brownlow_odds(_brownlow_event(), _brownlow_market(), "2026-05-26T10:00:00Z")
        row = rows[0]
        assert row["event_id"] == 9641792
        assert row["event_name"] == "2026 AFL Brownlow Medal"
        assert row["scraped_at"] == "2026-05-26T10:00:00Z"
        assert row["player"] == "Bailey Smith"
        assert row["odds"] == 4.0
        assert row["betting_status"] == "PRICED"
        assert row["market_status"] == "A"

    def test_missing_price_returns_none(self):
        market = _brownlow_market([{"name": "Bailey Smith", "price": {}}])
        rows = handler.parse_brownlow_odds(_brownlow_event(), market, "2026-05-26T10:00:00Z")
        assert rows[0]["odds"] is None


# ── _previous_brownlow_favourite ──────────────────────────────────────────────

class TestPreviousBrownlowFavourite:
    def test_returns_player_with_lowest_odds(self):
        rows = [_player_row("Bailey Smith", 4.0), _player_row("Nick Daicos", 5.0)]
        with patch.object(handler, "_read_jsonl", return_value=rows):
            assert handler._previous_brownlow_favourite("b", CURRENT, [PREV1]) == "Bailey Smith"

    def test_returns_none_when_no_history(self):
        assert handler._previous_brownlow_favourite("b", CURRENT, []) is None

    def test_skips_files_with_no_priced_selections(self):
        def fake_read(bucket, key):
            if key == PREV1:
                return [_player_row("Bailey Smith", None)]
            return [_player_row("Nick Daicos", 4.0)]

        with patch.object(handler, "_read_jsonl", side_effect=fake_read):
            assert handler._previous_brownlow_favourite("b", CURRENT, [PREV2, PREV1]) == "Nick Daicos"


# ── _check_brownlow_favourite_change ─────────────────────────────────────────

class TestCheckBrownlowFavouriteChange:
    def test_sends_alert_when_favourite_flips(self):
        players = [_player_row("Bailey Smith", 4.0), _player_row("Nick Daicos", 5.0)]
        with patch.object(handler, "_previous_brownlow_favourite", return_value="Nick Daicos"), \
             patch.object(handler, "send_slack") as mock_slack:
            handler._check_brownlow_favourite_change("b", players, CURRENT, [])
        mock_slack.assert_called_once_with(
            "2026 AFL Brownlow Medal - the favourite has changed to Bailey Smith",
            "SLACK_FAVOURITE_PARAM_NAME",
        )

    def test_no_alert_when_favourite_unchanged(self):
        players = [_player_row("Bailey Smith", 4.0), _player_row("Nick Daicos", 5.0)]
        with patch.object(handler, "_previous_brownlow_favourite", return_value="Bailey Smith"), \
             patch.object(handler, "send_slack") as mock_slack:
            handler._check_brownlow_favourite_change("b", players, CURRENT, [])
        mock_slack.assert_not_called()

    def test_no_alert_when_no_previous_data(self):
        players = [_player_row("Bailey Smith", 4.0)]
        with patch.object(handler, "_previous_brownlow_favourite", return_value=None), \
             patch.object(handler, "send_slack") as mock_slack:
            handler._check_brownlow_favourite_change("b", players, CURRENT, [])
        mock_slack.assert_not_called()

    def test_no_alert_when_all_odds_null(self):
        players = [_player_row("Bailey Smith", None)]
        with patch.object(handler, "_previous_brownlow_favourite") as mock_prev, \
             patch.object(handler, "send_slack") as mock_slack:
            handler._check_brownlow_favourite_change("b", players, CURRENT, [])
        mock_prev.assert_not_called()
        mock_slack.assert_not_called()


# ── Premiership helpers ───────────────────────────────────────────────────────

def _premiership_event(event_id=9641840, name="AFL Premiership Winner 2026", status="PRICED"):
    return {"id": event_id, "name": name, "startTime": 1790415000, "bettingStatus": status, "eventSort": "GRP1"}


def _premiership_market(selections=None):
    if selections is None:
        selections = [
            {"name": "Fremantle", "price": {"winPrice": 5.5}},
            {"name": "Geelong Cats", "price": {"winPrice": 5.75}},
            {"name": "Sydney Swans", "price": {"winPrice": 6.0}},
        ]
    return {"statusCode": "A", "selections": selections}


def _team_row(team, odds, event_id=9641840, event_name="AFL Premiership Winner 2026"):
    return {"event_id": event_id, "event_name": event_name, "team": team, "odds": odds}


# ── parse_premiership_odds ────────────────────────────────────────────────────

class TestParsePremiershipOdds:
    def test_returns_one_row_per_team(self):
        rows = handler.parse_premiership_odds(_premiership_event(), _premiership_market(), "2026-05-26T10:00:00Z")
        assert len(rows) == 3

    def test_row_fields(self):
        rows = handler.parse_premiership_odds(_premiership_event(), _premiership_market(), "2026-05-26T10:00:00Z")
        row = rows[0]
        assert row["event_id"] == 9641840
        assert row["event_name"] == "AFL Premiership Winner 2026"
        assert row["scraped_at"] == "2026-05-26T10:00:00Z"
        assert row["team"] == "Fremantle"
        assert row["odds"] == 5.5
        assert row["betting_status"] == "PRICED"
        assert row["market_status"] == "A"

    def test_missing_price_returns_none(self):
        market = _premiership_market([{"name": "Fremantle", "price": {}}])
        rows = handler.parse_premiership_odds(_premiership_event(), market, "2026-05-26T10:00:00Z")
        assert rows[0]["odds"] is None


# ── _previous_premiership_favourite ──────────────────────────────────────────

class TestPreviousPremiershipFavourite:
    def test_returns_team_with_lowest_odds(self):
        rows = [_team_row("Fremantle", 5.5), _team_row("Geelong Cats", 5.75)]
        with patch.object(handler, "_read_jsonl", return_value=rows):
            assert handler._previous_premiership_favourite("b", CURRENT, [PREV1]) == "Fremantle"

    def test_returns_none_when_no_history(self):
        assert handler._previous_premiership_favourite("b", CURRENT, []) is None

    def test_skips_files_with_no_priced_selections(self):
        def fake_read(bucket, key):
            if key == PREV1:
                return [_team_row("Fremantle", None)]
            return [_team_row("Geelong Cats", 5.75)]

        with patch.object(handler, "_read_jsonl", side_effect=fake_read):
            assert handler._previous_premiership_favourite("b", CURRENT, [PREV2, PREV1]) == "Geelong Cats"


# ── _check_premiership_favourite_change ──────────────────────────────────────

class TestCheckPremiershipFavouriteChange:
    def test_sends_alert_when_favourite_flips(self):
        teams = [_team_row("Fremantle", 5.5), _team_row("Geelong Cats", 5.75)]
        with patch.object(handler, "_previous_premiership_favourite", return_value="Geelong Cats"), \
             patch.object(handler, "send_slack") as mock_slack:
            handler._check_premiership_favourite_change("b", teams, CURRENT, [])
        mock_slack.assert_called_once_with(
            "AFL Premiership Winner 2026 - the favourite has changed to Fremantle",
            "SLACK_FAVOURITE_PARAM_NAME",
        )

    def test_no_alert_when_favourite_unchanged(self):
        teams = [_team_row("Fremantle", 5.5), _team_row("Geelong Cats", 5.75)]
        with patch.object(handler, "_previous_premiership_favourite", return_value="Fremantle"), \
             patch.object(handler, "send_slack") as mock_slack:
            handler._check_premiership_favourite_change("b", teams, CURRENT, [])
        mock_slack.assert_not_called()

    def test_no_alert_when_no_previous_data(self):
        teams = [_team_row("Fremantle", 5.5)]
        with patch.object(handler, "_previous_premiership_favourite", return_value=None), \
             patch.object(handler, "send_slack") as mock_slack:
            handler._check_premiership_favourite_change("b", teams, CURRENT, [])
        mock_slack.assert_not_called()

    def test_no_alert_when_all_odds_null(self):
        teams = [_team_row("Fremantle", None)]
        with patch.object(handler, "_previous_premiership_favourite") as mock_prev, \
             patch.object(handler, "send_slack") as mock_slack:
            handler._check_premiership_favourite_change("b", teams, CURRENT, [])
        mock_prev.assert_not_called()
        mock_slack.assert_not_called()


# ── Rising Star helpers ───────────────────────────────────────────────────────

def _rising_star_event(event_id=9863375, name="2026 AFL Rising Star", status="PRICED"):
    return {"id": event_id, "name": name, "startTime": 1789983000, "bettingStatus": status, "eventSort": "TNMT"}


def _rising_star_market(selections=None):
    if selections is None:
        selections = [
            {"name": "Jagga Smith (R1)", "price": {"winPrice": 2.5}},
            {"name": "Willem Duursma (R3)", "price": {"winPrice": 2.5}},
            {"name": "Phoenix Gothard (R6)", "price": {"winPrice": 8.0}},
        ]
    return {"statusCode": "A", "selections": selections}


def _rising_star_row(player, odds, event_id=9863375, event_name="2026 AFL Rising Star"):
    return {"event_id": event_id, "event_name": event_name, "player": player, "odds": odds}


# ── parse_rising_star_odds ────────────────────────────────────────────────────

class TestParseRisingStarOdds:
    def test_returns_one_row_per_player(self):
        rows = handler.parse_rising_star_odds(_rising_star_event(), _rising_star_market(), "2026-05-29T10:00:00Z")
        assert len(rows) == 3

    def test_row_fields(self):
        rows = handler.parse_rising_star_odds(_rising_star_event(), _rising_star_market(), "2026-05-29T10:00:00Z")
        row = rows[0]
        assert row["event_id"] == 9863375
        assert row["event_name"] == "2026 AFL Rising Star"
        assert row["scraped_at"] == "2026-05-29T10:00:00Z"
        assert row["player"] == "Jagga Smith (R1)"
        assert row["odds"] == 2.5
        assert row["betting_status"] == "PRICED"
        assert row["market_status"] == "A"

    def test_missing_price_returns_none(self):
        market = _rising_star_market([{"name": "Jagga Smith (R1)", "price": {}}])
        rows = handler.parse_rising_star_odds(_rising_star_event(), market, "2026-05-29T10:00:00Z")
        assert rows[0]["odds"] is None


# ── _previous_rising_star_favourite ──────────────────────────────────────────

class TestPreviousRisingStarFavourite:
    def test_returns_player_with_lowest_odds(self):
        rows = [_rising_star_row("Jagga Smith (R1)", 2.5), _rising_star_row("Phoenix Gothard (R6)", 8.0)]
        with patch.object(handler, "_read_jsonl", return_value=rows):
            assert handler._previous_rising_star_favourite("b", CURRENT, [PREV1]) == "Jagga Smith (R1)"

    def test_returns_none_when_no_history(self):
        assert handler._previous_rising_star_favourite("b", CURRENT, []) is None

    def test_skips_files_with_no_priced_selections(self):
        def fake_read(bucket, key):
            if key == PREV1:
                return [_rising_star_row("Jagga Smith (R1)", None)]
            return [_rising_star_row("Willem Duursma (R3)", 2.5)]

        with patch.object(handler, "_read_jsonl", side_effect=fake_read):
            assert handler._previous_rising_star_favourite("b", CURRENT, [PREV2, PREV1]) == "Willem Duursma (R3)"


# ── _check_rising_star_favourite_change ──────────────────────────────────────

class TestCheckRisingStarFavouriteChange:
    def test_sends_alert_when_favourite_flips(self):
        players = [_rising_star_row("Jagga Smith (R1)", 2.5), _rising_star_row("Phoenix Gothard (R6)", 8.0)]
        with patch.object(handler, "_previous_rising_star_favourite", return_value="Phoenix Gothard (R6)"), \
             patch.object(handler, "send_slack") as mock_slack:
            handler._check_rising_star_favourite_change("b", players, CURRENT, [])
        mock_slack.assert_called_once_with(
            "2026 AFL Rising Star - the favourite has changed to Jagga Smith (R1)",
            "SLACK_FAVOURITE_PARAM_NAME",
        )

    def test_no_alert_when_favourite_unchanged(self):
        players = [_rising_star_row("Jagga Smith (R1)", 2.5), _rising_star_row("Phoenix Gothard (R6)", 8.0)]
        with patch.object(handler, "_previous_rising_star_favourite", return_value="Jagga Smith (R1)"), \
             patch.object(handler, "send_slack") as mock_slack:
            handler._check_rising_star_favourite_change("b", players, CURRENT, [])
        mock_slack.assert_not_called()

    def test_no_alert_when_all_odds_null(self):
        players = [_rising_star_row("Jagga Smith (R1)", None)]
        with patch.object(handler, "_previous_rising_star_favourite") as mock_prev, \
             patch.object(handler, "send_slack") as mock_slack:
            handler._check_rising_star_favourite_change("b", players, CURRENT, [])
        mock_prev.assert_not_called()
        mock_slack.assert_not_called()


# ── Coleman Medal helpers ─────────────────────────────────────────────────────

def _coleman_event(event_id=9914007, name="2026 AFL Coleman Medal", status="PRICED"):
    return {"id": event_id, "name": name, "startTime": 1787650200, "bettingStatus": status, "eventSort": "TNMT"}


def _coleman_market(selections=None):
    if selections is None:
        selections = [
            {"name": "Ben King", "price": {"winPrice": 3.0}},
            {"name": "Jack Gunston", "price": {"winPrice": 4.0}},
            {"name": "Jeremy Cameron", "price": {"winPrice": 4.5}},
        ]
    return {"statusCode": "A", "selections": selections}


def _coleman_row(player, odds, event_id=9914007, event_name="2026 AFL Coleman Medal"):
    return {"event_id": event_id, "event_name": event_name, "player": player, "odds": odds}


# ── parse_coleman_odds ────────────────────────────────────────────────────────

class TestParseColemanOdds:
    def test_returns_one_row_per_player(self):
        rows = handler.parse_coleman_odds(_coleman_event(), _coleman_market(), "2026-05-29T10:00:00Z")
        assert len(rows) == 3

    def test_row_fields(self):
        rows = handler.parse_coleman_odds(_coleman_event(), _coleman_market(), "2026-05-29T10:00:00Z")
        row = rows[0]
        assert row["event_id"] == 9914007
        assert row["event_name"] == "2026 AFL Coleman Medal"
        assert row["scraped_at"] == "2026-05-29T10:00:00Z"
        assert row["player"] == "Ben King"
        assert row["odds"] == 3.0
        assert row["betting_status"] == "PRICED"
        assert row["market_status"] == "A"

    def test_missing_price_returns_none(self):
        market = _coleman_market([{"name": "Ben King", "price": {}}])
        rows = handler.parse_coleman_odds(_coleman_event(), market, "2026-05-29T10:00:00Z")
        assert rows[0]["odds"] is None


# ── _previous_coleman_favourite ───────────────────────────────────────────────

class TestPreviousColemanFavourite:
    def test_returns_player_with_lowest_odds(self):
        rows = [_coleman_row("Ben King", 3.0), _coleman_row("Jack Gunston", 4.0)]
        with patch.object(handler, "_read_jsonl", return_value=rows):
            assert handler._previous_coleman_favourite("b", CURRENT, [PREV1]) == "Ben King"

    def test_returns_none_when_no_history(self):
        assert handler._previous_coleman_favourite("b", CURRENT, []) is None

    def test_skips_files_with_no_priced_selections(self):
        def fake_read(bucket, key):
            if key == PREV1:
                return [_coleman_row("Ben King", None)]
            return [_coleman_row("Jack Gunston", 4.0)]

        with patch.object(handler, "_read_jsonl", side_effect=fake_read):
            assert handler._previous_coleman_favourite("b", CURRENT, [PREV2, PREV1]) == "Jack Gunston"


# ── _check_coleman_favourite_change ───────────────────────────────────────────

class TestCheckColemanFavouriteChange:
    def test_sends_alert_when_favourite_flips(self):
        players = [_coleman_row("Ben King", 3.0), _coleman_row("Jack Gunston", 4.0)]
        with patch.object(handler, "_previous_coleman_favourite", return_value="Jack Gunston"), \
             patch.object(handler, "send_slack") as mock_slack:
            handler._check_coleman_favourite_change("b", players, CURRENT, [])
        mock_slack.assert_called_once_with(
            "2026 AFL Coleman Medal - the favourite has changed to Ben King",
            "SLACK_FAVOURITE_PARAM_NAME",
        )

    def test_no_alert_when_favourite_unchanged(self):
        players = [_coleman_row("Ben King", 3.0), _coleman_row("Jack Gunston", 4.0)]
        with patch.object(handler, "_previous_coleman_favourite", return_value="Ben King"), \
             patch.object(handler, "send_slack") as mock_slack:
            handler._check_coleman_favourite_change("b", players, CURRENT, [])
        mock_slack.assert_not_called()

    def test_no_alert_when_all_odds_null(self):
        players = [_coleman_row("Ben King", None)]
        with patch.object(handler, "_previous_coleman_favourite") as mock_prev, \
             patch.object(handler, "send_slack") as mock_slack:
            handler._check_coleman_favourite_change("b", players, CURRENT, [])
        mock_prev.assert_not_called()
        mock_slack.assert_not_called()


# ── World Cup helpers ─────────────────────────────────────────────────────────

def _world_cup_event(event_id=7009197, name="World Cup 2026 Outrights", status="OPEN"):
    return {"id": event_id, "name": name, "startTime": 1782000000, "bettingStatus": status, "eventSort": "TNMT"}


def _world_cup_market(name="Winner 2026", market_id=163808009, selections=None):
    if selections is None:
        selections = [
            {"name": "Spain", "price": {"winPrice": 5.0}},
            {"name": "France", "price": {"winPrice": 6.0}},
            {"name": "England", "price": {"winPrice": 7.0}},
        ]
    return {"id": market_id, "name": name, "statusCode": "A", "selections": selections}


def _wc_row(selection, odds, market_name="Winner 2026"):
    return {"selection": selection, "odds": odds, "market_name": market_name}


# ── parse_world_cup_odds ──────────────────────────────────────────────────────

class TestParseWorldCupOdds:
    def test_returns_one_row_per_selection(self):
        rows = handler.parse_world_cup_odds(_world_cup_event(), _world_cup_market(), "2026-06-02T10:00:00Z")
        assert len(rows) == 3

    def test_row_fields(self):
        rows = handler.parse_world_cup_odds(_world_cup_event(), _world_cup_market(), "2026-06-02T10:00:00Z")
        row = rows[0]
        assert row["event_id"] == 7009197
        assert row["event_name"] == "World Cup 2026 Outrights"
        assert row["market_id"] == 163808009
        assert row["market_name"] == "Winner 2026"
        assert row["scraped_at"] == "2026-06-02T10:00:00Z"
        assert row["selection"] == "Spain"
        assert row["odds"] == 5.0
        assert row["betting_status"] == "OPEN"
        assert row["market_status"] == "A"

    def test_missing_price_returns_none(self):
        market = _world_cup_market(selections=[{"name": "Spain", "price": {}}])
        rows = handler.parse_world_cup_odds(_world_cup_event(), market, "2026-06-02T10:00:00Z")
        assert rows[0]["odds"] is None


# ── find_market ───────────────────────────────────────────────────────────────

class TestFindMarket:
    def test_returns_market_with_matching_name(self):
        markets = [_world_cup_market("Winner 2026"), _world_cup_market("Golden Boot Winner")]
        assert handler.find_market(markets, "Golden Boot Winner")["name"] == "Golden Boot Winner"

    def test_returns_none_when_absent(self):
        markets = [_world_cup_market("Winner 2026")]
        assert handler.find_market(markets, "Golden Ball Winner") is None


# ── _previous_world_cup_favourite ─────────────────────────────────────────────

class TestPreviousWorldCupFavourite:
    def test_returns_selection_with_lowest_odds(self):
        rows = [_wc_row("Spain", 5.0), _wc_row("France", 6.0)]
        with patch.object(handler, "_read_jsonl", return_value=rows):
            assert handler._previous_world_cup_favourite("b", "world-cup-winner", CURRENT, [PREV1]) == "Spain"

    def test_returns_none_when_no_history(self):
        assert handler._previous_world_cup_favourite("b", "world-cup-winner", CURRENT, []) is None

    def test_skips_files_with_no_priced_selections(self):
        def fake_read(bucket, key):
            if key == PREV1:
                return [_wc_row("Spain", None)]
            return [_wc_row("France", 6.0)]

        with patch.object(handler, "_read_jsonl", side_effect=fake_read):
            assert handler._previous_world_cup_favourite("b", "world-cup-winner", CURRENT, [PREV2, PREV1]) == "France"


# ── _check_world_cup_favourite_change ─────────────────────────────────────────

class TestCheckWorldCupFavouriteChange:
    def test_sends_alert_when_favourite_flips(self):
        rows = [_wc_row("Spain", 5.0), _wc_row("France", 6.0)]
        with patch.object(handler, "_previous_world_cup_favourite", return_value="France"), \
             patch.object(handler, "send_slack") as mock_slack:
            handler._check_world_cup_favourite_change("b", "world-cup-winner", rows, CURRENT, [], "World Cup Winner")
        mock_slack.assert_called_once_with(
            "World Cup Winner - the favourite has changed to Spain",
            "SLACK_FAVOURITE_PARAM_NAME",
        )

    def test_no_alert_when_favourite_unchanged(self):
        rows = [_wc_row("Spain", 5.0), _wc_row("France", 6.0)]
        with patch.object(handler, "_previous_world_cup_favourite", return_value="Spain"), \
             patch.object(handler, "send_slack") as mock_slack:
            handler._check_world_cup_favourite_change("b", "world-cup-winner", rows, CURRENT, [], "World Cup Winner")
        mock_slack.assert_not_called()

    def test_no_alert_when_no_previous_data(self):
        rows = [_wc_row("Spain", 5.0)]
        with patch.object(handler, "_previous_world_cup_favourite", return_value=None), \
             patch.object(handler, "send_slack") as mock_slack:
            handler._check_world_cup_favourite_change("b", "world-cup-winner", rows, CURRENT, [], "World Cup Winner")
        mock_slack.assert_not_called()

    def test_no_alert_when_all_odds_null(self):
        rows = [_wc_row("Spain", None)]
        with patch.object(handler, "_previous_world_cup_favourite") as mock_prev, \
             patch.object(handler, "send_slack") as mock_slack:
            handler._check_world_cup_favourite_change("b", "world-cup-winner", rows, CURRENT, [], "World Cup Winner")
        mock_prev.assert_not_called()
        mock_slack.assert_not_called()


# ── World Cup end-date cutoff in _scrape ──────────────────────────────────────

class TestWorldCupCutoff:
    def _run_scrape_at(self, when):
        import datetime as dtmod

        fake_dt = MagicMock()
        fake_dt.now.return_value = when
        fake_dt.fromtimestamp.side_effect = dtmod.datetime.fromtimestamp
        with patch.object(handler, "datetime", fake_dt), \
             patch.dict(os.environ, {"RESULTS_BUCKET": "b"}), \
             patch.object(handler, "time", MagicMock()), \
             patch.object(handler, "get_afl_events", return_value=[_event()]), \
             patch.object(handler, "get_h2h_market", return_value=_market()), \
             patch.object(handler.s3, "put_object"), \
             patch.object(handler, "_list_dated_keys", return_value=[]), \
             patch.object(handler, "_check_favourite_changes"), \
             patch.object(handler, "_scrape_brownlow", return_value=0), \
             patch.object(handler, "_scrape_premiership", return_value=0), \
             patch.object(handler, "_scrape_rising_star", return_value=0), \
             patch.object(handler, "_scrape_coleman", return_value=0), \
             patch.object(handler, "_scrape_world_cup", return_value={
                 "world-cup-winner": 0, "world-cup-golden-boot": 0, "world-cup-golden-ball": 0,
             }) as mock_wc, \
             patch.object(handler, "send_slack"):
            handler._scrape({}, None)
        return mock_wc

    def test_world_cup_scraped_on_cutoff_date(self):
        import datetime as dtmod

        when = dtmod.datetime(2026, 7, 21, 10, 0, tzinfo=dtmod.timezone.utc)
        assert self._run_scrape_at(when).called

    def test_world_cup_skipped_after_cutoff(self):
        import datetime as dtmod

        when = dtmod.datetime(2026, 7, 22, 10, 0, tzinfo=dtmod.timezone.utc)
        self._run_scrape_at(when).assert_not_called()


# ── _scrape_world_cup per-market counts ───────────────────────────────────────

class TestScrapeWorldCupCounts:
    def test_returns_count_per_market(self):
        import datetime as dtmod

        event = _world_cup_event()
        markets = [
            _world_cup_market("Winner 2026", selections=[
                {"name": "Spain", "price": {"winPrice": 5.0}},
            ]),
            _world_cup_market("Golden Boot Winner", selections=[
                {"name": "Kylian Mbappe", "price": {"winPrice": 6.5}},
                {"name": "Harry Kane", "price": {"winPrice": 8.0}},
            ]),
            _world_cup_market("Golden Ball Winner", selections=[
                {"name": "Michael Olise", "price": {"winPrice": 7.0}},
                {"name": "Jude Bellingham", "price": {"winPrice": 9.0}},
                {"name": "Vinicius Jr", "price": {"winPrice": 10.0}},
            ]),
        ]
        now = dtmod.datetime(2026, 6, 2, 10, 0, tzinfo=dtmod.timezone.utc)
        with patch.object(handler, "get_world_cup_event", return_value=event), \
             patch.object(handler, "get_world_cup_markets", return_value=markets), \
             patch.object(handler, "time", MagicMock()), \
             patch.object(handler.s3, "put_object"), \
             patch.object(handler, "_list_dated_keys", return_value=[]), \
             patch.object(handler, "_check_world_cup_favourite_change"):
            counts = handler._scrape_world_cup("b", now, "2026-06-02T10:00:00Z")
        assert counts == {
            "world-cup-winner": 1,
            "world-cup-golden-boot": 2,
            "world-cup-golden-ball": 3,
        }

    def test_returns_zeroed_counts_when_no_event(self):
        import datetime as dtmod

        now = dtmod.datetime(2026, 6, 2, 10, 0, tzinfo=dtmod.timezone.utc)
        with patch.object(handler, "get_world_cup_event", return_value=None):
            counts = handler._scrape_world_cup("b", now, "2026-06-02T10:00:00Z")
        assert counts == {
            "world-cup-winner": 0,
            "world-cup-golden-boot": 0,
            "world-cup-golden-ball": 0,
        }


# ── _melbourne_timestamp ──────────────────────────────────────────────────────

class TestMelbourneTimestamp:
    def test_formats_winter_as_aest(self):
        import datetime as dtmod

        # 02:00 UTC in June → 12:00 AEST (UTC+10)
        now = dtmod.datetime(2026, 6, 2, 2, 0, 0, tzinfo=dtmod.timezone.utc)
        assert handler._melbourne_timestamp(now) == "2026-06-02 12:00:00 AEST"

    def test_formats_summer_as_aedt(self):
        import datetime as dtmod

        # 02:00 UTC in January → 13:00 AEDT (UTC+11, daylight saving)
        now = dtmod.datetime(2026, 1, 15, 2, 0, 0, tzinfo=dtmod.timezone.utc)
        assert handler._melbourne_timestamp(now) == "2026-01-15 13:00:00 AEDT"
