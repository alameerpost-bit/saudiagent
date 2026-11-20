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

    def test_accepts_shadow_closing_only_with_verified_coverage_and_handoff(self):
        payload = audit(telegram="shadow_suppressed")
        payload["execution_mode"] = "shadow"
        payload["handoff"] = {
            "action": "written", "session_date": "2026-11-20",
            "run_id": "123", "commit_sha": "abc", "recommendation_count": 1,
        }
        payload["data"] = {
            "scan_coverage_decision": "accepted",
            "freshness_decision": "validated_for_session",
        }
        self.assertEqual(
            validate_audit(payload, expected_handoff_date="2026-11-19"),
            "shadow_validated",
        )

    def test_rejects_shadow_closing_without_handoff(self):
        payload = audit(telegram="shadow_suppressed")
        payload["execution_mode"] = "shadow"
        payload["data"] = {
            "scan_coverage_decision": "accepted",
            "freshness_decision": "validated_for_session",
        }
        with self.assertRaisesRegex(ValueError, "لم يسجل كتابة"):
            validate_audit(payload)

    def test_rejects_shadow_closing_with_insufficient_coverage(self):
        payload = audit(telegram="shadow_suppressed")
        payload["execution_mode"] = "shadow"
        payload["handoff"] = {
            "action": "written", "session_date": "2026-11-20",
            "run_id": "123", "commit_sha": "abc", "recommendation_count": 0,
        }
        payload["data"] = {
            "scan_coverage_decision": "below_minimum",
            "freshness_decision": "validated_for_session",
        }
        with self.assertRaisesRegex(ValueError, "تغطية مسح السوق"):
            validate_audit(payload)

    def test_accepts_shadow_review_with_consumed_handoff(self):
        payload = audit(telegram="shadow_suppressed")
        payload["operation"] = "review"
        payload["execution_mode"] = "shadow"
        payload["handoff"] = {
            "action": "loaded", "session_date": "2026-11-19",
            "run_id": "closing-run", "commit_sha": "abc", "recommendation_count": 1,
        }
        payload["data"] = {"freshness_decision": "validated_for_review_window"}
        self.assertEqual(validate_audit(payload), "shadow_validated")

    def test_rejects_shadow_review_with_wrong_handoff_date(self):
        payload = audit(telegram="shadow_suppressed")
        payload["operation"] = "review"
        payload["execution_mode"] = "shadow"
        payload["handoff"] = {
            "action": "loaded", "session_date": "2026-11-18",
            "run_id": "closing-run", "commit_sha": "abc", "recommendation_count": 1,
        }
        payload["data"] = {"freshness_decision": "validated_for_review_window"}
        with self.assertRaisesRegex(ValueError, "لا يطابق جلسة الإغلاق المتوقعة"):
            validate_audit(payload, expected_handoff_date="2026-11-19")

    def test_rejects_shadow_review_without_expected_date(self):
        payload = audit(telegram="shadow_suppressed")
        payload["operation"] = "review"
        payload["execution_mode"] = "shadow"
        payload["handoff"] = {
            "action": "loaded", "session_date": "2026-11-19",
            "run_id": "closing-run", "commit_sha": "abc", "recommendation_count": 0,
        }
        payload["data"] = {}
        with self.assertRaisesRegex(ValueError, "لم يُحدد تاريخ"):
            validate_audit(payload)

    def test_rejects_shadow_review_without_handoff_identity(self):
        payload = audit(telegram="shadow_suppressed")
        payload["operation"] = "review"
        payload["execution_mode"] = "shadow"
        payload["handoff"] = {
            "action": "loaded", "session_date": "2026-11-19",
            "recommendation_count": 0,
        }
        payload["data"] = {}
        with self.assertRaisesRegex(ValueError, "يفتقد هوية"):
            validate_audit(payload, expected_handoff_date="2026-11-19")

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
