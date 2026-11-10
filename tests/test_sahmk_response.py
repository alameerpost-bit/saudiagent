import unittest
from datetime import date
from unittest.mock import Mock, patch

import bot


class SahmkResponseTests(unittest.TestCase):
    def response(self, status, payload=None, json_error=None):
        response = Mock()
        response.status_code = status
        if json_error is not None:
            response.json.side_effect = json_error
        else:
            response.json.return_value = payload
        return response

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


if __name__ == "__main__":
    unittest.main()
