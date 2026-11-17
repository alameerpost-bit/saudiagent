import json
import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import bot


class SaudiExchangeCalendarTests(unittest.TestCase):
    def test_foundation_day_is_not_a_trading_day(self):
        self.assertFalse(bot.is_saudi_trading_day(date(2026, 2, 22)))

    def test_eid_break_is_skipped_when_finding_previous_session(self):
        self.assertEqual(
            bot.previous_trading_session(date(2026, 3, 24)),
            date(2026, 3, 16),
        )

    def test_weekend_and_holiday_are_both_skipped(self):
        self.assertEqual(
            bot.previous_trading_session(date(2026, 11, 22)),
            date(2026, 11, 19),
        )

    def test_fails_closed_outside_published_calendar_coverage(self):
        with self.assertRaises(bot.DataValidationError):
            bot.is_saudi_trading_day(date(2028, 1, 2))


class RecommendationHandoffTests(unittest.TestCase):
    def test_successful_empty_scan_has_explicit_provenance(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            bot, "SIGNALS_STATE_PATH", str(Path(tmp) / "previous_signals.json")
        ), patch.dict(os.environ, {
            "GITHUB_RUN_ID": "12345",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_SHA": "a" * 40,
        }):
            session = date(2026, 11, 16)
            now = datetime.fromisoformat("2026-11-17T16:20:00+03:00")
            bot.save_previous_signals([], session, now)
            loaded = bot.load_previous_signals(session)
            payload = json.loads(Path(bot.SIGNALS_STATE_PATH).read_text(encoding="utf-8"))

        self.assertEqual(loaded, [])
        self.assertEqual(payload["generation_status"], "success")
        self.assertEqual(payload["recommendation_count"], 0)
        self.assertEqual(payload["run_id"], "12345")
        self.assertEqual(payload["commit_sha"], "a" * 40)
        self.assertEqual(payload["session_date"], "2026-11-16")

    def test_rejects_handoff_when_recorded_count_does_not_match(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            bot, "SIGNALS_STATE_PATH", str(Path(tmp) / "previous_signals.json")
        ), patch.dict(os.environ, {
            "GITHUB_RUN_ID": "12345",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_SHA": "b" * 40,
        }):
            session = date(2026, 11, 16)
            bot.save_previous_signals([], session, datetime.fromisoformat("2026-11-17T16:20:00+03:00"))
            path = Path(bot.SIGNALS_STATE_PATH)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["recommendation_count"] = 1
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(bot.DataValidationError):
                bot.load_previous_signals(session)

    def test_rejects_legacy_or_unprovenanced_handoff(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            bot, "SIGNALS_STATE_PATH", str(Path(tmp) / "previous_signals.json")
        ):
            path = Path(bot.SIGNALS_STATE_PATH)
            path.write_text(json.dumps({
                "source": "daily_close_recommendations",
                "session_date": "2026-11-16",
                "signals": [],
            }), encoding="utf-8")
            with self.assertRaises(bot.DataValidationError):
                bot.load_previous_signals(date(2026, 11, 16))


if __name__ == "__main__":
    unittest.main()
