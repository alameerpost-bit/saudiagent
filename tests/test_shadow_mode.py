import os
import unittest
from unittest.mock import patch

import bot
from scripts.run_audit import RunAudit, set_active_audit


class ShadowTelegramSuppressionTests(unittest.TestCase):
    def test_shadow_send_never_calls_telegram_http_endpoint(self):
        previous = os.environ.get("SAUDI_AGENT_SHADOW")
        os.environ["SAUDI_AGENT_SHADOW"] = "true"
        audit = RunAudit()
        set_active_audit(audit)
        try:
            with patch.object(bot.requests, "post") as telegram_post:
                bot.send("رسالة اختبار داخل وضع Shadow")
            telegram_post.assert_not_called()
            self.assertEqual(audit.data["telegram"]["status"], "shadow_suppressed")
        finally:
            set_active_audit(None)
            if previous is None:
                os.environ.pop("SAUDI_AGENT_SHADOW", None)
            else:
                os.environ["SAUDI_AGENT_SHADOW"] = previous


if __name__ == "__main__":
    unittest.main()
