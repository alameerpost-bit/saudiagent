"""Shadow-mode orchestration for the eleven trader skills in Saudi Agent.

This module deliberately does not replace the established strategy. It reports
which capabilities are actually exercised by the production Python bot and
validates existing risk allocations without changing them.
"""
from __future__ import annotations

from typing import Any

SKILLS = (
    ("trader-analyst", "financial_analysis", "blocked", "لا يوجد موصل قوائم مالية سعودية موثوق ومتاح."),
    ("trader-screener", "technical_screening", "active_existing_logic", "يستخدم شروط الفحص الفني الحالية في bot.py؛ لا يغيرها."),
    ("trader-news", "disclosures_news", "blocked", "لا يوجد موصل موثق للأخبار والإفصاحات السعودية."),
    ("trader-alerts", "price_monitoring", "active_existing_logic", "تعمل مراجعة الأسعار الصباحية الحالية على رموز توصيات الإغلاق."),
    ("trader-tax", "cost_basis", "blocked", "لا يوجد سجل صفقات/تكلفة اقتناء موثق؛ لا تطبق قواعد الضرائب الأمريكية."),
    ("trader-rebalance", "portfolio_rebalance", "blocked", "بيانات المحفظة الفعلية غير موصولة للقراءة."),
    ("trader-budget", "capital_allocation", "active_existing_logic", "يستخدم سقوف رأس المال الحالية في bot.py دون تعديل."),
    ("trader-risk", "position_risk", "active_existing_logic", "يتحقق من حدود المخاطرة والتكلفة الحالية بعد التخصيص."),
    ("trader-broker", "broker_gateway", "blocked", "لا توجد واجهة أوامر سهم موثقة؛ التنفيذ الآلي معطل."),
    ("trader-copycat", "institutional_holdings", "reference_only", "بيانات 13F الأمريكية لا تمثل ملكيات تاسي."),
    ("trader-pelosi", "us_congress_disclosures", "reference_only", "إفصاحات أمريكية مرجعية فقط وليست إشارة للسوق السعودي."),
)

def registry() -> list[dict[str, str]]:
    """Return an immutable-in-practice serializable capability snapshot."""
    return [
        {"id": skill_id, "capability": capability, "status": status, "note": note}
        for skill_id, capability, status, note in SKILLS
    ]


def evaluate_closing_pipeline(
    technical_candidates: list[dict[str, Any]],
    allocated_signals: list[dict[str, Any]],
    *,
    max_total_risk: float,
    max_total_commitment: float,
) -> dict[str, Any]:
    """Audit existing screening and allocation outputs; never create/alter signals."""
    total_risk = sum(float(item.get("risk", 0) or 0) for item in allocated_signals)
    total_commitment = sum(float(item.get("commitment", 0) or 0) for item in allocated_signals)
    violations = []
    if total_risk > max_total_risk + 1e-8:
        violations.append("aggregate_risk_limit_exceeded")
    if total_commitment > max_total_commitment + 1e-8:
        violations.append("aggregate_commitment_limit_exceeded")
    if len(allocated_signals) > len(technical_candidates):
        violations.append("allocation_exceeds_screened_candidates")

    return {
        "version": 1,
        "mode": "shadow_validation",
        "recommendation_impact": "none",
        "technical_candidates": len(technical_candidates),
        "allocated_signals": len(allocated_signals),
        "total_risk": round(total_risk, 6),
        "max_total_risk": round(float(max_total_risk), 6),
        "total_commitment": round(total_commitment, 6),
        "max_total_commitment": round(float(max_total_commitment), 6),
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
        "skills": registry(),
    }
