import unittest

from scripts.skills_orchestrator import (
    evaluate_closing_pipeline,
    evaluate_morning_review,
    registry,
)


class SkillsOrchestratorTests(unittest.TestCase):
    def test_registry_contains_eleven_skills_and_safe_execution_states(self):
        skills = registry()
        self.assertEqual(len(skills), 11)
        self.assertEqual(len({item["id"] for item in skills}), 11)
        self.assertTrue(all(item["status"] in {
            "active_existing_logic", "blocked", "reference_only"
        } for item in skills))
        self.assertEqual(
            sum(item["status"] == "active_existing_logic" for item in skills), 4
        )

    def test_closing_pipeline_passes_without_mutating_signals(self):
        candidate = {"code": "2083", "entry": 46.18}
        signal = {
            "code": "2083", "entry": 46.18,
            "risk": 1.25, "commitment": 140.0,
        }
        before = dict(signal)
        result = evaluate_closing_pipeline(
            [candidate], [signal],
            max_total_risk=10.0,
            max_total_commitment=1000.0,
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["recommendation_impact"], "none")
        self.assertEqual(signal, before)

    def test_closing_pipeline_fails_if_existing_risk_caps_are_breached(self):
        result = evaluate_closing_pipeline(
            [{"code": "2222"}],
            [{"code": "2222", "risk": 11.0, "commitment": 100.0}],
            max_total_risk=10.0,
            max_total_commitment=1000.0,
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("aggregate_risk_limit_exceeded", result["violations"])

    def test_morning_review_is_read_only_and_records_same_symbols(self):
        result = evaluate_morning_review(["2083", "2222"])
        self.assertEqual(result["reviewed_symbols"], ["2083", "2222"])
        self.assertFalse(result["automatic_execution"])
        self.assertFalse(result["broker_orders_enabled"])


if __name__ == "__main__":
    unittest.main()
