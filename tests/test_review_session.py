import unittest
from datetime import date, datetime
from unittest.mock import patch

import bot


class ReviewSessionDateTests(unittest.TestCase):
    def test_review_uses_previous_session_even_when_workflow_starts_after_close(self):
        # GitHub may start the scheduled 10:15 review hours late.
        now = datetime.fromisoformat("2026-11-17T19:52:00+03:00")
        with patch.object(bot, "load_previous_signals", return_value=[]) as load:
            message = bot.review_next_session(now)

        load.assert_called_once_with(date(2026, 11, 16))
        self.assertIn("إغلاق جلسة 2026-11-16", message)

    def test_previous_session_skips_friday_and_saturday(self):
        self.assertEqual(
            bot.previous_trading_session(date(2026, 11, 22)),
            date(2026, 11, 19),
        )

    def test_previous_session_for_tuesday_is_monday(self):
        self.assertEqual(
            bot.previous_trading_session(date(2026, 11, 17)),
            date(2026, 11, 16),
        )


if __name__ == "__main__":
    unittest.main()
