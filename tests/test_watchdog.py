import unittest
from datetime import date

from scripts.watchdog import choose_action


TODAY = date(2026, 11, 17)


def run(run_id, *, status="completed", conclusion="success", name="Daily signals",
        created="2026-11-17T13:15:00Z", attempt=1):
    return {
        "id": run_id,
        "status": status,
        "conclusion": conclusion,
        "name": name,
        "created_at": created,
        "run_attempt": attempt,
        "html_url": f"https://github.com/example/runs/{run_id}",
    }


class WatchdogDecisionTests(unittest.TestCase):
    def test_dispatch_when_no_run_exists(self):
        self.assertEqual(choose_action([], TODAY)[0], "dispatch_fallback")

    def test_wait_while_scheduled_run_is_active(self):
        self.assertEqual(
            choose_action([run(1, status="in_progress", conclusion=None)], TODAY)[0],
            "wait",
        )

    def test_do_not_dispatch_after_success(self):
        self.assertEqual(choose_action([run(1)], TODAY)[0], "done")

    def test_retry_failed_jobs_once(self):
        self.assertEqual(
            choose_action([run(1, conclusion="failure", attempt=1)], TODAY)[0],
            "retry_failed_jobs",
        )

    def test_alert_after_retry_was_already_used(self):
        self.assertEqual(
            choose_action([run(1, conclusion="failure", attempt=2)], TODAY)[0],
            "alert_failed",
        )

    def test_fallback_is_not_dispatched_twice(self):
        fallback = run(2, status="in_progress", conclusion=None,
                       name="Daily signals watchdog-fallback")
        self.assertEqual(choose_action([fallback], TODAY)[0], "wait_fallback")

    def test_old_run_does_not_suppress_today_fallback(self):
        yesterday = run(1, created="2026-11-16T13:15:00Z")
        self.assertEqual(choose_action([yesterday], TODAY)[0], "dispatch_fallback")


if __name__ == "__main__":
    unittest.main()
