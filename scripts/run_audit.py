"""Structured, secret-safe metadata for one Saudi Agent process run."""
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_ACTIVE = None
_RIYADH = ZoneInfo("Asia/Riyadh")


def set_active_audit(audit):
    global _ACTIVE
    _ACTIVE = audit


def record_telegram(status, error=None):
    if _ACTIVE is not None:
        _ACTIVE.data["telegram"] = {
            "status": status,
            "error": _ACTIVE.clean_error(error) if error else None,
        }


class RunAudit:
    def __init__(self, workflow=None):
        now = datetime.now(_RIYADH)
        self.data = {
            "schema_version": 1,
            "run_id": os.getenv("GITHUB_RUN_ID") or None,
            "run_attempt": int(os.getenv("GITHUB_RUN_ATTEMPT", "1")),
            "workflow": workflow or os.getenv("GITHUB_WORKFLOW") or "local",
            "operation": sys.argv[1] if len(sys.argv) > 1 else "run",
            "commit_sha": os.getenv("GITHUB_SHA") or None,
            "started_at": now.isoformat(),
            "finished_at": None,
            "timezone": "Asia/Riyadh",
            "status": "running",
            "data": {
                "provider": "SAHMK",
                "technical_data_provider": "Yahoo Finance",
                "quote_timestamp": None,
                "retrieved_at": None,
                "delayed": None,
                "freshness_decision": "unavailable",
                "universe_count": None,
                "scanned_count": 0,
                "rejected_count": 0,
                "no_signal_count": 0,
                "scan_coverage": None,
                "scan_coverage_decision": "unavailable",
                "rejection_reasons": {},
            },
            "recommendations": {
                "count": 0,
                "emission_decision": "not_attempted",
                "no_emission_reason": None,
            },
            "telegram": {"status": "not_attempted", "error": None},
            "errors": [],
        }

    def clean_error(self, error):
        value = str(error).strip() or error.__class__.__name__
        for key in ("SAHMK_API_KEY", "TELEGRAM_TOKEN", "GH_TOKEN"):
            secret = os.getenv(key)
            if secret:
                value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"bot\d+:[A-Za-z0-9_-]+", "[REDACTED_TELEGRAM_TOKEN]", value)
        return value[:1200]

    def fail(self, error):
        self.data["status"] = "failed"
        self.data["errors"].append({
            "type": error.__class__.__name__,
            "summary": self.clean_error(error),
        })
        self.data["recommendations"]["emission_decision"] = "not_emitted"
        self.data["recommendations"]["no_emission_reason"] = self.clean_error(error)

    def capture(self, namespace):
        market = namespace.get("MARKET_CONTEXT")
        universe = namespace.get("MARKET_UNIVERSE_CONTEXT")
        records = namespace.get("MARKET_AUDIT") or []
        if universe:
            self.data["data"]["universe_count"] = universe.get("count")
            self.data["data"]["quote_timestamp"] = (
                universe.get("timestamp").isoformat()
                if universe.get("timestamp") is not None else None
            )
            self.data["data"]["delayed"] = universe.get("is_delayed")
            self.data["data"]["freshness_decision"] = "validated_for_session"
        if market:
            self.data["data"]["quote_timestamp"] = market.get("timestamp").isoformat()
            self.data["data"]["delayed"] = market.get("is_delayed")
            self.data["data"]["freshness_decision"] = "validated_for_session"
        self.data["data"]["retrieved_at"] = datetime.now(_RIYADH).isoformat()
        stock_records = [item for item in records if item.get("label") != "تاسي"]
        self.data["data"]["scanned_count"] = len(stock_records)
        self.data["data"]["rejected_count"] = sum(
            item.get("status") != "OK" for item in stock_records
        )
        self.data["data"]["no_signal_count"] = sum(
            item.get("signal_status") == "NO_SIGNAL" for item in stock_records
        )
        universe_count = self.data["data"]["universe_count"]
        if universe_count:
            coverage = sum(item.get("status") == "OK" for item in stock_records) / universe_count
            self.data["data"]["scan_coverage"] = round(coverage, 4)
            self.data["data"]["scan_coverage_decision"] = (
                "accepted" if coverage >= 0.80 else "below_minimum"
            )
        reasons = Counter()
        for item in stock_records:
            if item.get("status") != "OK":
                reason = item.get("error") or "data_validation_failed"
                reasons[self.clean_error(reason)[:180]] += 1
            if item.get("signal_status") == "NO_SIGNAL":
                for reason in item.get("reasons") or ["technical_conditions_not_met"]:
                    reasons[str(reason)[:180]] += 1
        self.data["data"]["rejection_reasons"] = dict(reasons)
        self.data["recommendations"]["count"] = namespace.get("_AUDIT_RECOMMENDATION_COUNT", 0)
        if self.data["recommendations"]["count"]:
            self.data["recommendations"]["emission_decision"] = (
                "emitted" if self.data["telegram"]["status"] == "sent" else "generated_not_confirmed_sent"
            )
        elif self.data["recommendations"]["emission_decision"] == "not_attempted":
            if self.data["operation"] == "review":
                self.data["recommendations"]["emission_decision"] = "not_applicable_review_only"
            else:
                self.data["recommendations"]["emission_decision"] = "not_emitted"
                self.data["recommendations"]["no_emission_reason"] = (
                    "no_recommendations_or_non_trading_skip"
                )

    def write(self, path=None):
        self.data["finished_at"] = datetime.now(_RIYADH).isoformat()
        if self.data["status"] == "running":
            self.data["status"] = "success"
        destination = Path(path or os.getenv("SAUDI_AGENT_AUDIT_PATH", "data/audit/run.json"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"[Run audit] {destination}")
        return destination
