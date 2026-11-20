#!/usr/bin/env python3
"""Fail a workflow unless the application completed and Telegram confirmed delivery."""
import json
import os
import sys
from pathlib import Path


def validate_audit(payload, expected_run_id=None, expected_commit_sha=None, expected_handoff_date=None):
    if not isinstance(payload, dict):
        raise ValueError("ملف التدقيق ليس كائناً JSON صالحاً.")
    if payload.get("schema_version") != 1:
        raise ValueError("إصدار مخطط ملف التدقيق غير مدعوم.")
    if expected_run_id and str(payload.get("run_id")) != str(expected_run_id):
        raise ValueError("ملف التدقيق لا يخص تشغيل GitHub Actions الحالي.")
    if expected_commit_sha and payload.get("commit_sha") != expected_commit_sha:
        raise ValueError("ملف التدقيق لا يخص commit التشغيل الحالي.")

    status = payload.get("status")
    operation = payload.get("operation")
    if status == "skipped" and payload.get("skip_reason") == "non_trading_day":
        if operation not in ("run", "review"):
            raise ValueError("تخطي غير متوقع لنوع العملية.")
        return "skipped_non_trading_day"

    if status != "success":
        errors = payload.get("errors") or []
        detail = errors[0].get("summary", "لا يوجد سبب مسجل") if errors else "حالة التدقيق ليست success"
        raise ValueError(f"التشغيل لم يكتمل وظيفياً: {detail}")

    telegram = payload.get("telegram") or {}
    if payload.get("execution_mode") == "shadow":
        if telegram.get("status") != "shadow_suppressed":
            raise ValueError("وضع Shadow لم يثبت منع Telegram فعلياً.")
        if payload.get("run_id") in (None, "") or payload.get("commit_sha") in (None, ""):
            raise ValueError("ملف Shadow يفتقد هوية التشغيل أو SHA المصدر.")
        handoff = payload.get("handoff") or {}
        data = payload.get("data") or {}
        if operation == "run":
            if handoff.get("action") != "written":
                raise ValueError("تشغيل الإغلاق لم يسجل كتابة ملف التسليم.")
            if handoff.get("run_id") != payload.get("run_id") or handoff.get("commit_sha") != payload.get("commit_sha"):
                raise ValueError("هوية ملف التسليم لا تطابق هوية تشغيل Shadow.")
            if data.get("scan_coverage_decision") != "accepted":
                raise ValueError("تغطية مسح السوق لم تجتز الحد الأدنى في Shadow.")
            if data.get("freshness_decision") != "validated_for_session":
                raise ValueError("توقيت بيانات الإغلاق لم يجتز التحقق في Shadow.")
        elif operation == "review":
            if handoff.get("action") != "loaded":
                raise ValueError("المراجعة الصباحية لم تقرأ ملف تسليم الإغلاق.")
            if not expected_handoff_date:
                raise ValueError("لم يُحدد تاريخ جلسة الإغلاق المتوقع للتحقق المستقل.")
            if handoff.get("session_date") != expected_handoff_date:
                raise ValueError(
                    "تاريخ ملف التسليم لا يطابق جلسة الإغلاق المتوقعة "
                    f"({expected_handoff_date})."
                )
            if not handoff.get("run_id") or not handoff.get("commit_sha"):
                raise ValueError("ملف التسليم يفتقد هوية تشغيل الإغلاق أو SHA المصدر.")
            count = handoff.get("recommendation_count")
            if not isinstance(count, int) or count < 0:
                raise ValueError("عدد توصيات ملف التسليم غير صالح.")
            if count > 0 and data.get("freshness_decision") != "validated_for_review_window":
                raise ValueError("أسعار المراجعة لم تجتز نافذة حداثة البيانات.")
        else:
            raise ValueError("عملية Shadow غير معروفة.")
        return "shadow_validated"
    if telegram.get("status") != "sent":
        detail = telegram.get("error") or telegram.get("status") or "غير معروف"
        raise ValueError(f"لم يؤكد Telegram الإرسال: {detail}")

    recommendations = payload.get("recommendations") or {}
    decision = recommendations.get("emission_decision")
    allowed = {"emitted", "sent_no_recommendations", "not_applicable_review_only"}
    if decision not in allowed:
        raise ValueError(f"قرار إصدار الرسالة غير صالح: {decision!r}")
    return decision


def main():
    path = Path(os.getenv("SAUDI_AGENT_AUDIT_PATH", "data/audit/run.json"))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        result = validate_audit(
            payload,
            expected_run_id=os.getenv("EXPECTED_RUN_ID"),
            expected_commit_sha=os.getenv("EXPECTED_COMMIT_SHA"),
            expected_handoff_date=os.getenv("EXPECTED_HANDOFF_DATE"),
        )
    except (OSError, ValueError, TypeError) as exc:
        print(f"[فشل التحقق الوظيفي] {exc}", file=sys.stderr)
        raise SystemExit(1)
    print(f"[نجاح التحقق الوظيفي] {result}")


if __name__ == "__main__":
    main()
