#!/usr/bin/env python3
"""Idempotent watchdog for the Saudi Agent daily recommendation workflow."""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Riyadh")
API = os.environ.get("GH_API_URL", "https://api.github.com").rstrip("/")
REPO = os.environ.get("GH_REPOSITORY")
TOKEN = os.environ.get("GH_TOKEN")
WORKFLOW = os.environ.get("SAUDI_AGENT_WATCHDOG_WORKFLOW", "daily.yml")
TASK_LABEL = os.environ.get("SAUDI_AGENT_WATCHDOG_LABEL", "توصيات الإغلاق")


def request_json(url, method="GET", payload=None):
    if not TOKEN:
        raise RuntimeError("GH_TOKEN غير مضبوط.")
    data = None if payload is None else json.dumps(payload).encode()
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read()
        return json.loads(raw.decode()) if raw else None


def telegram(message):
    bot_token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        raise RuntimeError("TELEGRAM_TOKEN أو TELEGRAM_CHAT_ID غير مضبوط.")
    body = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        result = json.loads(response.read().decode())
    if result.get("ok") is not True:
        raise RuntimeError("Telegram لم يؤكد إرسال التنبيه.")


def created_window(day):
    """Limit the API query to one Riyadh calendar day, independent of run history."""
    start = datetime(day.year, day.month, day.day, tzinfo=TZ)
    end = start + timedelta(days=1)
    return f"{start.isoformat()}..{end.isoformat()}"


def local_run_date(run):
    created = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
    return created.astimezone(TZ).date()


def is_watchdog_fallback(run):
    return "watchdog-fallback" in (run.get("name") or "").lower()


def choose_action(runs, today):
    """Pure decision function, separately unit-tested."""
    todays = [run for run in runs if local_run_date(run) == today]
    normal = [run for run in todays if not is_watchdog_fallback(run)]
    fallback = [run for run in todays if is_watchdog_fallback(run)]

    if normal:
        latest = max(normal, key=lambda item: item["created_at"])
        status, conclusion = latest.get("status"), latest.get("conclusion")
        if status in ("queued", "in_progress"):
            return "wait", latest
        if status == "completed" and conclusion == "success":
            return "done", latest
        if status == "completed" and conclusion == "failure":
            if int(latest.get("run_attempt") or 1) < 2:
                return "retry_failed_jobs", latest
            return "alert_failed", latest
        if status == "completed":
            return "alert_terminal", latest
        return "wait", latest

    if fallback:
        latest = max(fallback, key=lambda item: item["created_at"])
        if latest.get("status") in ("queued", "in_progress"):
            return "wait_fallback", latest
        if latest.get("status") == "completed" and latest.get("conclusion") == "success":
            return "done", latest
        return "alert_fallback_failed", latest

    return "dispatch_fallback", None


def validated_run_list(data):
    """Refuse recovery decisions when GitHub returned a partial or malformed page."""
    if not isinstance(data, dict):
        raise RuntimeError("استجابة GitHub لقائمة التشغيل غير صالحة.")
    runs = data.get("workflow_runs")
    if (not isinstance(runs, list)
            or data.get("total_count", len(runs)) > len(runs)
            or len(runs) >= 100):
        raise RuntimeError("قائمة تشغيل GitHub غير مكتملة؛ أوقفنا الاسترداد التلقائي منعاً لإطلاق تشغيل مكرر.")
    return runs


def main():
    if not REPO:
        raise RuntimeError("GH_REPOSITORY غير مضبوط.")
    today = datetime.now(TZ).date()
    query = urllib.parse.urlencode({
        "branch": "main",
        "per_page": 100,
        "created": created_window(today),
    })
    url = f"{API}/repos/{REPO}/actions/workflows/{WORKFLOW}/runs?{query}"
    data = request_json(url)
    runs = validated_run_list(data)
    action, run = choose_action(runs, today)
    run_url = (run or {}).get("html_url", "")

    if action in ("done", "wait", "wait_fallback"):
        print(f"Watchdog action={action}; run={run_url}")
        return

    if action == "dispatch_fallback":
        dispatch_url = f"{API}/repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches"
        request_json(dispatch_url, method="POST", payload={
            "ref": "main",
            "inputs": {"watchdog_fallback": "true"},
        })
        telegram(
            f"⚠️ مراقب Saudi Agent: لم يُعثر على تشغيل {TASK_LABEL} اليوم "
            f"({today}) حتى موعد الفحص.\n"
            "تم إطلاق تشغيل احتياطي واحد؛ لن يكرر المراقب الإطلاق إذا ظهر تشغيل "
            "watchdog-fallback لليوم نفسه.\nلم يتم تنفيذ أي أوامر تداول."
        )
        print("Watchdog fallback dispatch requested.")
        return

    if action == "retry_failed_jobs":
        retry_url = (
            f"{API}/repos/{REPO}/actions/runs/{run['id']}/rerun-failed-jobs"
        )
        request_json(retry_url, method="POST")
        telegram(
            f"⚠️ تعثر تشغيل Saudi Agent ({TASK_LABEL}).\n"
            f"الحالة: {run.get('conclusion')} | المحاولة: {run.get('run_attempt', 1)}\n"
            "طلب المراقب إعادة المهام الفاشلة مرة واحدة.\n"
            f"التشغيل: {run_url}\nلم يتم تنفيذ أي أوامر تداول."
        )
        print(f"Requested failed-job retry for run {run['id']}.")
        return

    telegram(
        f"🚨 تعثر تشغيل Saudi Agent ({TASK_LABEL}) ولم تتم إعادة المحاولة تلقائياً.\n"
        f"السبب: {action}\nالتشغيل: {run_url}\n"
        "يرجى مراجعة GitHub Actions. لم يتم تنفيذ أي أوامر تداول."
    )
    print(f"Watchdog action={action}; run={run_url}", file=sys.stderr)
    if action in ("alert_failed", "alert_terminal", "alert_fallback_failed"):
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Watchdog failed: {exc}", file=sys.stderr)
        try:
            telegram(
                f"🚨 تعذر على مراقب Saudi Agent التحقق من تشغيل {TASK_LABEL}.\n"
                f"السبب: {str(exc)[:1000]}\nيرجى فحص Schedule watchdog."
            )
        except Exception as alert_exc:
            print(f"Telegram alert failed: {alert_exc}", file=sys.stderr)
        raise
