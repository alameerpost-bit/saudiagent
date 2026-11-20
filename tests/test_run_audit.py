import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.run_audit import RunAudit, record_telegram, set_active_audit


class RunAuditTests(unittest.TestCase):
    def test_writes_required_metadata_without_secrets(self):
        audit = RunAudit(workflow="Daily signals")
        audit.data["recommendations"]["count"] = 2
        audit.data["telegram"]["status"] = "sent"
        with tempfile.TemporaryDirectory() as tmp:
            path = audit.write(Path(tmp) / "audit.json")
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["timezone"], "Asia/Riyadh")
        self.assertEqual(payload["recommendations"]["count"], 2)
        self.assertEqual(payload["status"], "success")
        self.assertIn("started_at", payload)
        self.assertIn("finished_at", payload)

    def test_captures_market_and_scan_counts(self):
        audit = RunAudit()
        audit.capture({
            "MARKET_CONTEXT": {"timestamp": __import__("datetime").datetime.now().astimezone(),
                               "is_delayed": True},
            "MARKET_UNIVERSE_CONTEXT": {"count": 50},
            "MARKET_AUDIT": [
                {"label": "تاسي", "status": "OK"},
                {"label": "سهم أ", "status": "OK"},
                {"label": "سهم ب (0002)", "status": "FAILED", "error": "quote unavailable"},
            ],
            "_AUDIT_RECOMMENDATION_COUNT": 1,
        })
        self.assertEqual(audit.data["data"]["universe_count"], 50)
        self.assertEqual(audit.data["data"]["scanned_count"], 2)
        self.assertEqual(audit.data["data"]["rejected_count"], 1)
        self.assertEqual(
            audit.data["data"]["rejected_symbols"][0]["symbol"], "0002"
        )
        self.assertTrue(audit.data["data"]["delayed"])

    def test_captures_skills_orchestration_without_enabling_execution(self):
        audit = RunAudit()
        audit.capture({
            "MARKET_AUDIT": [],
            "SKILLS_PIPELINE_AUDIT": {
                "mode": "shadow_validation",
                "status": "PASS",
                "skills": [
                    {"id": "trader-screener", "status": "active_existing_logic"},
                    {"id": "trader-news", "status": "blocked"},
                ],
            },
        })
        self.assertEqual(audit.data["skills"]["active_existing_logic"], 1)
        self.assertEqual(audit.data["skills"]["blocked_or_reference_only"], 1)
        self.assertFalse(audit.data["skills"]["automatic_execution"])
        self.assertFalse(audit.data["skills"]["broker_orders_enabled"])

    def test_does_not_invent_retrieval_timestamp(self):
        audit = RunAudit()
        audit.capture({
            "SAHMK_LAST_QUOTE_TIMESTAMP": "2026-11-17T10:00:00+03:00",
            "SAHMK_LAST_QUOTE_DELAYED": True,
            "SAHMK_LAST_RETRIEVED_AT": None,
            "MARKET_AUDIT": [],
        })
        self.assertEqual(
            audit.data["data"]["quote_timestamp"], "2026-11-17T10:00:00+03:00"
        )
        self.assertEqual(
            audit.data["data"]["freshness_decision"], "validated_for_review_window"
        )
        self.assertIsNone(audit.data["data"]["retrieved_at"])

    def test_redacts_configured_secrets(self):
        os.environ["SAHMK_API_KEY"] = "secret-value"
        try:
            audit = RunAudit()
            audit.fail(RuntimeError("provider rejected secret-value"))
            self.assertNotIn("secret-value", audit.data["errors"][0]["summary"])
        finally:
            os.environ.pop("SAHMK_API_KEY", None)

    def test_records_telegram_result(self):
        audit = RunAudit()
        set_active_audit(audit)
        try:
            record_telegram("sent")
            self.assertEqual(audit.data["telegram"]["status"], "sent")
        finally:
            set_active_audit(None)

    def test_shadow_mode_is_explicit_in_audit(self):
        previous = os.environ.get("SAUDI_AGENT_SHADOW")
        os.environ["SAUDI_AGENT_SHADOW"] = "true"
        try:
            audit = RunAudit()
            self.assertEqual(audit.data["execution_mode"], "shadow")
            self.assertTrue(audit.data["run_id"])
        finally:
            if previous is None:
                os.environ.pop("SAUDI_AGENT_SHADOW", None)
            else:
                os.environ["SAUDI_AGENT_SHADOW"] = previous


if __name__ == "__main__":
    unittest.main()
