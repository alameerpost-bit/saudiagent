import unittest

from scripts.verify_run_audit import validate_audit


def audit(status="success", telegram="sent", decision="sent_no_recommendations"):
    return {
        "schema_version": 1,
        "status": status,
        "operation": "run",
        "run_id": "123",
        "commit_sha": "abc",
        "telegram": {"status": telegram},
        "recommendations": {"emission_decision": decision},
        "errors": [],
    }


class VerifyRunAuditTests(unittest.TestCase):
    def test_accepts_confirmed_no_signal_message(self):
        self.assertEqual(validate_audit(audit(), "123", "abc"), "sent_no_recommendations")

    def test_rejects_green_workflow_without_telegram_confirmation(self):
        with self.assertRaisesRegex(ValueError, "لم يؤكد Telegram"):
            validate_audit(audit(telegram="not_attempted"))

    def test_rejects_stale_run_audit(self):
        with self.assertRaisesRegex(ValueError, "لا يخص تشغيل"):
            validate_audit(audit(), "456", "abc")

    def test_accepts_explicit_non_trading_day_skip(self):
        payload = audit(status="skipped", telegram="not_attempted")
        payload["skip_reason"] = "non_trading_day"
        self.assertEqual(validate_audit(payload), "skipped_non_trading_day")

    def test_rejects_other_skip_reason(self):
        payload = audit(status="skipped", telegram="not_attempted")
        payload["skip_reason"] = "too_early"
        with self.assertRaises(ValueError):
            validate_audit(payload)


if __name__ == "__main__":
    unittest.main()
