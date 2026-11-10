import unittest
from datetime import date, datetime
from unittest.mock import Mock, patch

import bot


class SahmkResponseTests(unittest.TestCase):
    def setUp(self):
        bot.SAHMK_MIN_REQUEST_INTERVAL = 0
        bot._SAHMK_LAST_REQUEST_AT = None
        bot.SAHMK_MAX_RETRIES = 2

    def response(self, status, payload=None, json_error=None):
        response = Mock()
        response.status_code = status
        if json_error is not None:
            response.json.side_effect = json_error
        else:
            response.json.return_value = payload
        return response

    def test_review_accepts_quote_approximately_15_minutes_delayed(self):
        now = datetime.fromisoformat("2026-08-12T10:15:00+03:00")
        timestamp = bot._saudi_timestamp("2026-08-12T10:00:00+03:00")
        bot.validate_review_quote_timestamp(timestamp, now)

    def test_review_rejects_stale_quote_outside_expected_delay_window(self):
        now = datetime.fromisoformat("2026-08-12T10:15:00+03:00")
        timestamp = bot._saudi_timestamp("2026-08-12T09:50:00+03:00")
        with self.assertRaisesRegex(bot.DataValidationError, "عمر السعر"):
            bot.validate_review_quote_timestamp(timestamp, now)

    def test_review_command_rejects_time_before_1015(self):
        now = datetime.fromisoformat("2026-08-12T10:14:00+03:00")
        with self.assertRaisesRegex(bot.DataValidationError, "10:15"):
            bot.review_next_session(now)

    def test_reads_documented_flat_quote_payload(self):
        payload = {
            "symbol": "2222",
            "price": 26.6,
            "updated_at": "2026-08-12T12:20:00+00:00",
            "is_delayed": True,
        }
        response = self.response(200, payload)
        with patch.object(bot, "SAHMK_API_KEY", "test-key"), patch.object(
            bot.requests, "get", return_value=response
        ) as get:
            result = bot.sahmk_get("quote/2222/")

        self.assertEqual(result, payload)
        self.assertEqual(get.call_args.kwargs["headers"], {"X-API-Key": "test-key"})
        self.assertEqual(get.call_args.kwargs["timeout"], bot.SAHMK_TIMEOUT)

    def test_parses_documented_market_summary_fields(self):
        payload = {
            "index": "TASI",
            "timestamp": "2026-08-12T12:20:00+00:00",
            "index_value": 10844.12,
            "index_change_percent": 0.1,
            "is_delayed": False,
        }
        response = self.response(200, payload)
        with patch.object(bot, "SAHMK_API_KEY", "test-key"), patch.object(
            bot.requests, "get", return_value=response
        ):
            result = bot.fetch_sahmk_market_summary(date(2026, 8, 12))

        self.assertEqual(result["index_value"], 10844.12)
        self.assertEqual(result["change_percent"], 0.1)
        self.assertEqual(result["timestamp"].date(), date(2026, 8, 12))
        self.assertFalse(result["is_delayed"])

    def test_parses_wrapped_quote_payload_with_market_timestamp(self):
        payload = {
            "provider": "SAHMK",
            "retrievedAt": "2026-08-12T12:25:00+00:00",
            "data": {
                "symbol": "2222",
                "price": 26.6,
                "timestamp": "2026-08-12T12:20:00+00:00",
                "is_delayed": True,
            },
        }
        with patch.object(bot, "sahmk_get", return_value=payload):
            result = bot.fetch_sahmk_quote("2222", date(2026, 8, 12))

        self.assertEqual(result["price"], 26.6)
        self.assertEqual(result["updated_at"].strftime("%H:%M"), "15:20")
        self.assertTrue(result["is_delayed"])

    def test_parses_wrapped_tasi_market_summary(self):
        payload = {
            "provider": "SAHMK",
            "retrievedAt": "2026-08-12T12:25:00+00:00",
            "data": {
                "index": "TASI",
                "timestamp": "2026-08-12T12:20:00+00:00",
                "index_value": 10844.12,
                "index_change_percent": 0.1,
                "is_delayed": False,
            },
        }
        with patch.object(bot, "sahmk_get", return_value=payload):
            result = bot.fetch_sahmk_market_summary(date(2026, 8, 12))

        self.assertEqual(result["index_value"], 10844.12)
        self.assertEqual(result["change_percent"], 0.1)
        self.assertEqual(result["timestamp"].date(), date(2026, 8, 12))
        self.assertFalse(result["is_delayed"])

    def test_accepts_delayed_quote_after_market_close_for_next_session(self):
        payload = {
            "data": {
                "symbol": "2222",
                "price": 26.6,
                "timestamp": "2026-08-12T12:20:00+00:00",
                "is_delayed": True,
            }
        }
        with patch.object(bot, "sahmk_get", return_value=payload):
            result = bot.fetch_sahmk_quote("2222", date(2026, 8, 12))
        self.assertTrue(result["is_delayed"])
        self.assertEqual(result["updated_at"].strftime("%H:%M"), "15:20")

    def test_rejects_quote_timestamp_before_close_confirmation(self):
        payload = {
            "data": {
                "symbol": "2222",
                "price": 26.6,
                "timestamp": "2026-08-12T11:00:00+00:00",
                "is_delayed": True,
            }
        }
        with patch.object(bot, "sahmk_get", return_value=payload):
            with self.assertRaisesRegex(bot.DataValidationError, "قبل اكتمال الإغلاق"):
                bot.fetch_sahmk_quote("2222", date(2026, 8, 12))

    def test_buy_signal_diagnostics_explain_unmet_conditions(self):
        from types import SimpleNamespace
        row = SimpleNamespace(ma20=10.0, ma50=11.0, Close=9.0, rsi=75.0, volr=0.5)
        reasons = bot.buy_signal_reasons(row)
        self.assertEqual(len(reasons), 4)
        self.assertFalse(bot.buy_signal(row))

    def test_telegram_no_signal_message_lists_each_stock_reason(self):
        bot.MARKET_CONTEXT = {}
        bot.MARKET_AUDIT = [
            {"label": "أرامكو (2222)", "timestamp": None, "delayed": True,
             "status": "OK", "signal_status": "NO_SIGNAL",
             "reasons": ["RSI خارج النطاق 45–70 (الحالي: 72.0)"]},
        ]
        message = bot.format_message([])
        self.assertIn("أسباب عدم الدخول", message)
        self.assertIn("أرامكو (2222)", message)
        self.assertIn("RSI خارج النطاق", message)

    def test_does_not_use_proxy_retrieval_time_as_quote_timestamp(self):
        payload = {
            "retrievedAt": "2026-08-12T12:25:00+00:00",
            "data": {"symbol": "2222", "price": 26.6},
        }
        with patch.object(bot, "sahmk_get", return_value=payload):
            with self.assertRaisesRegex(bot.DataValidationError, "وقت تحديث السوق"):
                bot.fetch_sahmk_quote("2222", date(2026, 8, 12))

    def test_retries_rate_limit_using_provider_wait_message(self):
        limited = self.response(
            429,
            {"error": {"code": "RATE_LIMITED",
                       "message": "Request was throttled. Expected available in 33 seconds."}},
        )
        limited.headers = {}
        success_payload = {"symbol": "2350", "price": 1.5}
        success = self.response(200, success_payload)
        with patch.object(bot, "SAHMK_API_KEY", "test-key"), patch.object(
            bot.requests, "get", side_effect=[limited, success]
        ) as get, patch.object(bot.time, "sleep") as sleep:
            result = bot.sahmk_get("quote/2350/")

        self.assertEqual(result, success_payload)
        self.assertEqual(get.call_count, 2)
        sleep.assert_called_once_with(33.0)

    def test_retries_rate_limit_using_retry_after_header(self):
        limited = self.response(429, {"error": {"code": "RATE_LIMITED"}})
        limited.headers = {"Retry-After": "7"}
        success_payload = {"symbol": "2222", "price": 26.6}
        success = self.response(200, success_payload)
        with patch.object(bot, "SAHMK_API_KEY", "test-key"), patch.object(
            bot.requests, "get", side_effect=[limited, success]
        ), patch.object(bot.time, "sleep") as sleep:
            result = bot.sahmk_get("quote/2222/")

        self.assertEqual(result, success_payload)
        sleep.assert_called_once_with(7.0)

    def test_reports_structured_http_error_code_and_message(self):
        response = self.response(
            429,
            {
                "error": {
                    "code": "RATE_LIMITED",
                    "message": "Request rate limit exceeded.",
                },
                "detail": "Request rate limit exceeded.",
            },
        )
        with patch.object(bot, "SAHMK_API_KEY", "test-key"), patch.object(
            bot.requests, "get", return_value=response
        ):
            with self.assertRaisesRegex(
                bot.DataValidationError,
                r"HTTP 429.*RATE_LIMITED.*Request rate limit exceeded",
            ) as raised:
                bot.sahmk_get("quote/2222/")
        self.assertEqual(
            str(raised.exception).count("Request rate limit exceeded."), 1
        )

    def test_reports_non_json_http_error_without_leaking_credentials(self):
        response = self.response(503, json_error=ValueError("not JSON"))
        with patch.object(bot, "SAHMK_API_KEY", "test-key"), patch.object(
            bot.requests, "get", return_value=response
        ):
            with self.assertRaisesRegex(
                bot.DataValidationError, r"HTTP 503.*غير صالحة كـ JSON"
            ) as raised:
                bot.sahmk_get("quote/2222/")

        self.assertNotIn("test-key", str(raised.exception))

    def test_rejects_non_object_success_payload(self):
        response = self.response(200, [{"price": 26.6}])
        with patch.object(bot, "SAHMK_API_KEY", "test-key"), patch.object(
            bot.requests, "get", return_value=response
        ):
            with self.assertRaisesRegex(
                bot.DataValidationError, "تنسيق استجابة غير متوقع"
            ):
                bot.sahmk_get("quote/2222/")

    def test_market_audit_prints_each_verified_instrument(self):
        from contextlib import redirect_stdout
        from io import StringIO
        stamp = bot._saudi_timestamp("2026-08-12T12:20:00+00:00")
        bot.MARKET_AUDIT = [
            {"label": "تاسي", "timestamp": stamp, "delayed": True, "status": "OK"},
            {"label": "أرامكو (2222)", "timestamp": stamp, "delayed": True, "status": "OK"},
        ]
        output = StringIO()
        with redirect_stdout(output):
            bot.print_market_audit()
        captured = output.getvalue()
        self.assertIn("2/11 ناجح", captured)
        self.assertIn("تاسي | OK", captured)
        self.assertIn("أرامكو (2222) | OK", captured)

    def test_telegram_message_includes_coverage_when_no_signals(self):
        bot.MARKET_CONTEXT = {}
        bot.MARKET_AUDIT = [
            {"label": "تاسي", "timestamp": None, "delayed": True, "status": "OK"},
        ]
        message = bot.format_message([])
        self.assertIn("تغطية التحقق: 1/11", message)


if __name__ == "__main__":
    unittest.main()
