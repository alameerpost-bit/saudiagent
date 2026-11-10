#!/usr/bin/env python3
"""
بوت إشارات تداول للسوق السعودي (تداول) - أداة دعم قرار وليست نصيحة مالية.

الأوامر:
    python bot.py backtest   # اختبار تاريخي للاستراتيجية ومقارنة تاسي
    python bot.py run        # توليد الإشارات وإرسالها بعد إغلاق السوق وتحديث الأسعار
    python bot.py daemon     # انتظار وقت الإرسال اليومي (16:00 بتوقيت الرياض)
"""
import os
import re
import sys
import time
from datetime import datetime, time as time_of_day, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf

# ===================== الإعدادات =====================
SYMBOLS = {
    "2222": "أرامكو", "1120": "الراجحي", "1180": "الأهلي", "1150": "الإنماء",
    "2010": "سابك", "7010": "stc", "1211": "معادن", "2280": "المراعي",
    "5110": "الكهرباء", "2350": "كيان",
}
CAPITAL = 1000                 # رأس المال بالريال
RISK_PER_TRADE = 0.01          # حد الخطر الاسمي لكل إشارة قبل توزيع السقف الإجمالي
MAX_TOTAL_RISK = CAPITAL * 0.01  # إجمالي الخطر الأقصى لجميع الإشارات: 10 ريالات
MAX_TOTAL_POSITION = CAPITAL   # إجمالي تكلفة المراكز المقترحة لا يتجاوز 1,000 ريال
MAX_POSITION = 0.20            # حد إضافي للمركز الواحد: 20% من رأس المال
ATR_STOP_MULT = 2.0            # وقف الخسارة = 2 × ATR
ATR_TARGET_MULT = 3.0          # الهدف = 3 × ATR
FEE = 0.0012                   # عمولة + رسوم تقريبية لكل جهة
SLIPPAGE_RATE = 0.001          # انزلاق افتراضي 0.10% لكل جهة في الاختبار وتقدير الخطر
SEND_TIME = (16, 0)            # 16:00 بتوقيت الرياض، بعد نهاية التداول والتحديث
TRADING_DAYS = {6, 0, 1, 2, 3}  # الأحد-الخميس (Python: الإثنين=0 ... الأحد=6)
TZ = ZoneInfo("Asia/Riyadh")
TASI_SYMBOL = "^TASI.SR"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SAHMK_API_KEY = os.getenv("SAHMK_API_KEY")
SAHMK_BASE_URL = os.getenv("SAHMK_BASE_URL", "https://api.sahmk.sa/api/v1")
SAHMK_TIMEOUT = 20
# سهمك يفرض حدوداً على معدل الطلبات؛ نباعد بين طلبات الأسهم لتقليل HTTP 429.
SAHMK_MIN_REQUEST_INTERVAL = float(os.getenv("SAHMK_MIN_REQUEST_INTERVAL", "6"))
SAHMK_MAX_RETRIES = int(os.getenv("SAHMK_MAX_RETRIES", "2"))
_SAHMK_LAST_REQUEST_AT = None
MARKET_CONTEXT = None
MARKET_AUDIT = []


class DataValidationError(RuntimeError):
    """خطأ يمنع إصدار إشارة عندما لا يمكن التحقق من بيانات الجلسة."""


class SignalAllocationError(RuntimeError):
    """توجد إشارات فنية لكن لا يمكن تخصيص سهم واحد لها ضمن القيود."""


def _sahmk_wait_for_request_slot() -> None:
    """فرض فاصل أدنى بين طلبات سهمك ضمن عملية التشغيل الواحدة."""
    global _SAHMK_LAST_REQUEST_AT
    now = time.monotonic()
    if _SAHMK_LAST_REQUEST_AT is not None:
        remaining = SAHMK_MIN_REQUEST_INTERVAL - (now - _SAHMK_LAST_REQUEST_AT)
        if remaining > 0:
            time.sleep(remaining)
    _SAHMK_LAST_REQUEST_AT = time.monotonic()


def _sahmk_retry_delay(response, payload) -> float:
    """استخراج مهلة إعادة المحاولة من Retry-After أو رسالة سهمك."""
    headers = getattr(response, "headers", {}) or {}
    retry_after = headers.get("Retry-After") or headers.get("retry-after")
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except (TypeError, ValueError):
            pass
    message = str(payload or "")
    match = re.search(r"(?:available|retry|wait)\s+in\s+(\d+(?:\\.\d+)?)\s+seconds?", message, re.I)
    if match:
        return max(0.0, float(match.group(1)))
    return 2.0


def sahmk_get(path: str, params: dict | None = None) -> dict:
    """طلب موثق من سهمك مع تنظيم المعدل وإعادة محاولة 429؛ لا ننتقل إلى Yahoo عند فشل المصدر الأساسي."""
    if not SAHMK_API_KEY:
        raise DataValidationError("مفتاح SAHMK_API_KEY غير مضبوط في GitHub Secrets.")

    response = None
    for attempt in range(SAHMK_MAX_RETRIES + 1):
        _sahmk_wait_for_request_slot()
        try:
            response = requests.get(
                f"{SAHMK_BASE_URL.rstrip('/')}/{path.lstrip('/')}",
                headers={"X-API-Key": SAHMK_API_KEY},
                params=params,
                timeout=SAHMK_TIMEOUT,
            )
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            raise DataValidationError(f"سهمك: تعذر جلب {path} (HTTP {status or 'network'}): {exc}") from exc

        if getattr(response, "status_code", None) == 429 and attempt < SAHMK_MAX_RETRIES:
            try:
                rate_payload = response.json()
            except (TypeError, ValueError):
                rate_payload = None
            delay = _sahmk_retry_delay(response, rate_payload)
            print(
                f"[سهمك] حد الطلبات لطلب {path}؛ إعادة المحاولة بعد {delay:g} ثانية "
                f"({attempt + 1}/{SAHMK_MAX_RETRIES})."
            )
            time.sleep(delay)
            continue
        break

    status = getattr(response, "status_code", None)
    try:
        # Decode first: SAHMK returns useful error.code/error.message JSON on
        # non-2xx responses, which raise_for_status() would otherwise discard.
        payload = response.json()
    except (TypeError, ValueError) as exc:
        if status is not None and status >= 400:
            raise DataValidationError(
                f"سهمك: تعذر جلب {path} (HTTP {status}): استجابة خطأ غير صالحة كـ JSON."
            ) from exc
        raise DataValidationError(f"سهمك: استجابة غير صالحة من {path}.") from exc

    if not isinstance(payload, dict):
        raise DataValidationError(f"سهمك: تنسيق استجابة غير متوقع من {path}.")
    error = payload.get("error")
    if (status is not None and status >= 400) or error:
        if isinstance(error, dict):
            details = [
                str(value)
                for value in (error.get("code"), error.get("message"))
                if value
            ]
            detail = ": ".join(details)
        else:
            detail = str(error) if error else ""
        fallback = payload.get("detail")
        if fallback and str(fallback) not in detail:
            detail = f"{detail}: {fallback}" if detail else str(fallback)
        detail = detail or "استجابة خطأ من الواجهة."
        status_text = f"HTTP {status}" if status is not None else "خطأ"
        raise DataValidationError(f"سهمك: تعذر جلب {path} ({status_text}): {detail}")
    return payload


def _saudi_timestamp(value) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize(TZ)
    return timestamp.tz_convert(TZ)


def _sahmk_response_data(payload: dict) -> tuple[dict, dict]:
    """Return the data object and its envelope for both flat and wrapped API responses."""
    nested = payload.get("data")
    if isinstance(nested, dict):
        return nested, payload
    return payload, payload


def _sahmk_timestamp(data: dict, envelope: dict):
    # Prefer the market-data timestamp. retrievedAt is deliberately not used:
    # it can be the proxy retrieval time rather than the exchange quote time.
    return data.get("timestamp") or data.get("updated_at") or envelope.get("timestamp")


def _sahmk_delayed(data: dict, envelope: dict) -> bool:
    value = data.get("is_delayed")
    if value is None:
        value = envelope.get("is_delayed")
    # Fail closed: an unspecified delay flag must never be presented as realtime.
    return True if value is None else bool(value)


def fetch_sahmk_quote(code: str, expected_date) -> dict:
    """السعر الأساسي للإشارة من سهمك مع دعم الاستجابة المتداخلة والتحقق من تاريخها."""
    payload = sahmk_get(f"quote/{code}/")
    data, envelope = _sahmk_response_data(payload)
    price = data.get("price")
    updated = _sahmk_timestamp(data, envelope)
    if price is None or not updated:
        raise DataValidationError(
            f"{code}: استجابة سهمك لا تحتوي price ووقت تحديث السوق (timestamp/updated_at)."
        )
    try:
        updated_at = _saudi_timestamp(updated)
        price = float(price)
    except (TypeError, ValueError) as exc:
        raise DataValidationError(f"{code}: سعر أو توقيت سهمك غير صالح.") from exc
    if not pd.notna(price) or price <= 0:
        raise DataValidationError(f"{code}: سعر سهمك غير صالح.")
    if updated_at.date() != expected_date:
        raise DataValidationError(
            f"{code}: آخر تحديث من سهمك {updated_at.date()} وليس جلسة {expected_date}؛ أوقفنا الإشارات."
        )
    return {
        "price": price,
        "updated_at": updated_at,
        "is_delayed": _sahmk_delayed(data, envelope),
    }


def fetch_sahmk_market_summary(expected_date) -> dict:
    """ملخص تاسي من سهمك مع دعم الاستجابة المتداخلة والتحقق من تاريخها."""
    payload = sahmk_get("market/summary/", params={"index": "TASI"})
    data, envelope = _sahmk_response_data(payload)
    updated = _sahmk_timestamp(data, envelope)
    if not updated:
        raise DataValidationError("تاسي: سهمك لم يرجع timestamp أو updated_at لوقت بيانات السوق.")
    try:
        updated_at = _saudi_timestamp(updated)
        index_value = float(data["index_value"])
        change_pct = float(data["index_change_percent"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DataValidationError("تاسي: استجابة سهمك ناقصة أو غير صالحة.") from exc
    if updated_at.date() != expected_date:
        raise DataValidationError(
            f"تاسي: آخر تحديث من سهمك {updated_at.date()} وليس جلسة {expected_date}؛ أوقفنا الإشارات."
        )
    return {
        "index_value": index_value,
        "change_percent": change_pct,
        "timestamp": updated_at,
        "is_delayed": _sahmk_delayed(data, envelope),
    }


# ===================== البيانات والمؤشرات =====================
def _local_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=TZ)
    return now.astimezone(TZ)


def latest_expected_session(now: datetime | None = None):
    """آخر جلسة كان يفترض اكتمال بياناتها حسب أيام السوق ووقت الإغلاق."""
    local = _local_now(now)
    day = local.date()
    if day.weekday() in TRADING_DAYS and local.time() < time_of_day(*SEND_TIME):
        day -= timedelta(days=1)
    while day.weekday() not in TRADING_DAYS:
        day -= timedelta(days=1)
    return day


def validate_session_data(df: pd.DataFrame, code: str,
                          now: datetime | None = None) -> None:
    if df is None or df.empty:
        raise DataValidationError(f"{code}: لا توجد بيانات تاريخية للتحقق منها.")
    if not df.index.is_monotonic_increasing:
        raise DataValidationError(f"{code}: تواريخ البيانات غير مرتبة.")
    try:
        actual = pd.Timestamp(df.index[-1]).date()
    except Exception as exc:
        raise DataValidationError(f"{code}: تعذر قراءة تاريخ آخر شمعة.") from exc
    expected = latest_expected_session(now)
    if actual != expected:
        raise DataValidationError(
            f"{code}: أحدث جلسة في البيانات {actual}، والمتوقعة {expected}; أوقفنا الإشارات."
        )


def fetch(code: str, period: str = "2y") -> pd.DataFrame:
    symbol = code if code.startswith("^") else f"{code}.SR"
    try:
        df = yf.Ticker(symbol).history(period=period, auto_adjust=False)
    except Exception as exc:
        raise DataValidationError(f"{code}: تعذر جلب البيانات: {exc}") from exc
    if df is None or df.empty:
        raise DataValidationError(f"{code}: مصدر البيانات أعاد جدولاً فارغاً.")
    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise DataValidationError(f"{code}: أعمدة البيانات مفقودة: {', '.join(missing)}.")
    df = df[required].dropna().sort_index()
    if df.empty:
        raise DataValidationError(f"{code}: لا توجد شموع مكتملة بعد تنظيف البيانات.")
    return df


def fetch_index(period: str = "5y") -> pd.DataFrame:
    try:
        df = yf.Ticker(TASI_SYMBOL).history(period=period, auto_adjust=True)
    except Exception as exc:
        raise DataValidationError(f"تاسي: تعذر جلب بيانات {TASI_SYMBOL}: {exc}") from exc
    if df is None or df.empty or "Close" not in df.columns:
        raise DataValidationError(f"تاسي: مصدر البيانات أعاد بيانات فارغة أو غير مكتملة.")
    df = df[["Close"]].dropna().sort_index()
    if df.empty:
        raise DataValidationError("تاسي: لا توجد أسعار إغلاق مكتملة.")
    return df


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ma20"] = df.Close.rolling(20).mean()
    df["ma50"] = df.Close.rolling(50).mean()
    d = df.Close.diff()
    gain = d.clip(lower=0).rolling(14).mean()
    loss = (-d.clip(upper=0)).rolling(14).mean()
    df["rsi"] = 100 - 100 / (1 + gain / loss)
    tr = pd.concat([
        df.High - df.Low,
        (df.High - df.Close.shift()).abs(),
        (df.Low - df.Close.shift()).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()
    df["volr"] = df.Volume / df.Volume.rolling(20).mean()
    return df


def buy_signal(r) -> bool:
    return bool(
        r.ma20 > r.ma50 and r.Close > r.ma20
        and 45 < r.rsi < 70 and r.volr >= 1.0
    )


# ===================== إدارة المخاطر =====================
def estimated_loss_per_share(entry: float, stop: float) -> float:
    """خسارة تقديرية للسهم عند الوقف، شاملة الرسوم والانزلاق على الجهتين."""
    if not (pd.notna(entry) and pd.notna(stop) and entry > stop > 0):
        raise DataValidationError("سعر دخول أو وقف غير صالح لحساب المخاطرة.")
    entry_fill = entry * (1 + SLIPPAGE_RATE)
    stop_fill = stop * (1 - SLIPPAGE_RATE)
    return entry_fill * (1 + FEE) - stop_fill * (1 - FEE)


def estimated_entry_cost_per_share(entry: float) -> float:
    return entry * (1 + SLIPPAGE_RATE) * (1 + FEE)


def position_size(entry: float, stop: float) -> tuple[int, float]:
    risk_per_share = estimated_loss_per_share(entry, stop)
    cost_per_share = estimated_entry_cost_per_share(entry)
    if risk_per_share <= 0 or cost_per_share <= 0:
        return 0, 0.0
    risk_budget = CAPITAL * RISK_PER_TRADE
    position_budget = CAPITAL * MAX_POSITION
    shares_by_risk = int(risk_budget / risk_per_share)
    shares_by_position = int(position_budget / cost_per_share)
    shares = max(0, min(shares_by_risk, shares_by_position))
    return shares, shares * entry


def _allocate_integer_shares(items: list[dict], risk_budget: float,
                             cost_budget: float) -> list[int]:
    """توزيع تدريجي للأسهم الصحيحة دون تجاوز أي من السقوف الإجمالية."""
    allocated = [0] * len(items)
    used_risk = used_cost = 0.0
    while True:
        changed = False
        for index, item in enumerate(items):
            if allocated[index] >= item["shares"]:
                continue
            next_risk = used_risk + item["risk_per_share"]
            next_cost = used_cost + item["cost_per_share"]
            if next_risk <= risk_budget + 1e-9 and next_cost <= cost_budget + 1e-9:
                allocated[index] += 1
                used_risk, used_cost = next_risk, next_cost
                changed = True
        if not changed:
            break
    return allocated


def allocate_positions(candidates: list[dict]) -> list[dict]:
    """توزيع موحد للإشارات مع احترام الخطر والتكلفة الإجماليين للأسهم الصحيحة."""
    sized = []
    for candidate in candidates:
        shares, _ = position_size(candidate["entry"], candidate["stop"])
        if shares <= 0:
            continue
        item = dict(candidate)
        item["shares"] = shares
        item["risk_per_share"] = estimated_loss_per_share(item["entry"], item["stop"])
        item["cost_per_share"] = estimated_entry_cost_per_share(item["entry"])
        sized.append(item)

    counts = _allocate_integer_shares(sized, MAX_TOTAL_RISK, MAX_TOTAL_POSITION)
    allocated = []
    for item, shares in zip(sized, counts):
        if shares <= 0:
            continue
        item["shares"] = shares
        item["value"] = shares * item["entry"]
        item["risk"] = shares * item["risk_per_share"]
        item["commitment"] = shares * item["cost_per_share"]
        allocated.append(item)

    if sum(x["risk"] for x in allocated) > MAX_TOTAL_RISK + 1e-8:
        raise AssertionError("تجاوز سقف المخاطرة الإجمالي بعد التوزيع.")
    if sum(x["commitment"] for x in allocated) > MAX_TOTAL_POSITION + 1e-8:
        raise AssertionError("تجاوز سقف تكلفة المراكز الإجمالي بعد التوزيع.")
    return allocated


# ===================== الإشارات اليومية =====================
def generate_signals(now: datetime | None = None) -> list[dict]:
    local = _local_now(now)
    if local.weekday() not in TRADING_DAYS or local.time() < time_of_day(*SEND_TIME):
        raise DataValidationError("الإشارات اليومية متاحة فقط بعد 16:00 بتوقيت الرياض في يوم تداول.")

    candidates = []
    expected = latest_expected_session(local)
    global MARKET_CONTEXT, MARKET_AUDIT
    MARKET_AUDIT = []
    print(f"[تدقيق البيانات] الجلسة المتوقعة: {expected}; تاسي + {len(SYMBOLS)} أسهم.")
    MARKET_CONTEXT = fetch_sahmk_market_summary(expected)
    MARKET_AUDIT.append({
        "label": "تاسي", "timestamp": MARKET_CONTEXT["timestamp"],
        "delayed": MARKET_CONTEXT["is_delayed"], "status": "OK",
    })
    print(
        f"[تدقيق البيانات] تاسي: OK | {MARKET_CONTEXT['timestamp'].strftime('%Y-%m-%d %H:%M %Z')} "
        f"| {'متأخرة' if MARKET_CONTEXT['is_delayed'] else 'لحظية'}"
    )

    for code, name in SYMBOLS.items():
        try:
            # OHLCV من Yahoo لحساب المؤشرات؛ سعر الإشارة وتوقيته من سهمك.
            df = fetch(code, "1y")
            validate_session_data(df, code, local)
            indicators = add_indicators(df).dropna()
            if indicators.empty or pd.Timestamp(indicators.index[-1]).date() != expected:
                raise DataValidationError(f"{code}: تعذر التحقق من مؤشرات جلسة {expected}.")
            quote = fetch_sahmk_quote(code, expected)
            r = indicators.iloc[-1]
            yahoo_close = float(r.Close)
            if abs(quote["price"] - yahoo_close) / yahoo_close > 0.05:
                raise DataValidationError(
                    f"{code}: اختلاف سعر سهمك عن سجل Yahoo بأكثر من 5%؛ أوقفنا الإشارة للمراجعة."
                )
            MARKET_AUDIT.append({
                "label": f"{name} ({code})", "timestamp": quote["updated_at"],
                "delayed": quote["is_delayed"], "status": "OK",
            })
            print(
                f"[تدقيق البيانات] {name} ({code}): OK | "
                f"{quote['updated_at'].strftime('%Y-%m-%d %H:%M %Z')} | "
                f"{'متأخرة' if quote['is_delayed'] else 'لحظية'}"
            )
            if not buy_signal(r):
                continue
            entry = quote["price"]
            stop = entry - ATR_STOP_MULT * float(r.atr)
            target = entry + ATR_TARGET_MULT * float(r.atr)
            estimated_loss_per_share(entry, stop)
            candidates.append(dict(
                code=code, name=name, entry=entry, stop=stop, target=target,
                rsi=float(r.rsi), date=pd.Timestamp(indicators.index[-1]).date(),
                quote_time=quote["updated_at"], delayed=quote["is_delayed"],
            ))
        except Exception as exc:
            MARKET_AUDIT.append({
                "label": f"{name} ({code})", "timestamp": None,
                "delayed": None, "status": "FAILED", "error": str(exc),
            })
            print(f"[تدقيق البيانات] {name} ({code}): FAILED | {exc}")
            print_market_audit()
            raise

    print_market_audit()
    allocated = allocate_positions(candidates)
    if candidates and not allocated:
        raise SignalAllocationError(
            "رُصدت إشارات فنية لكن تعذر تخصيص سهم واحد ضمن سقفي المخاطرة والمراكز؛ لم تُرسل نتيجة فارغة."
        )
    return allocated


def print_market_audit() -> None:
    """سجل موجز قابل للتدقيق لتغطية تاسي والأسهم العشرة."""
    records = MARKET_AUDIT or []
    ok = sum(item["status"] == "OK" for item in records)
    failed = sum(item["status"] != "OK" for item in records)
    print(f"[تدقيق البيانات] الملخص: {ok}/{len(SYMBOLS) + 1} ناجح | {failed} فاشل.")
    for item in records:
        stamp = item["timestamp"]
        stamp_text = stamp.strftime("%Y-%m-%d %H:%M %Z") if stamp is not None else "لا يوجد"
        delay = ("متأخرة" if item["delayed"] else "لحظية") if item["delayed"] is not None else "غير محدد"
        print(f"[تدقيق البيانات] {item['label']} | {item['status']} | {stamp_text} | {delay}")


def format_message(signals: list[dict]) -> str:
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    market = MARKET_CONTEXT or {}
    market_line = "تاسي: بيانات سهمك غير متاحة."
    if market:
        delay_label = "متأخرة" if market["is_delayed"] else "لحظية"
        market_line = (
            f"تاسي: {market['index_value']:.2f} "
            f"({market['change_percent']:+.2f}%) | {delay_label} "
            f"| تحديث: {market['timestamp'].strftime('%Y-%m-%d %H:%M %Z')}"
        )
    if not signals:
        return (f"📊 إشارات تداول - {today}\n{market_line}\n"
                "لا توجد فرص شراء بعد التحقق من بيانات سهمك وجلسة جميع الأسهم.")
    total_risk = sum(s["risk"] for s in signals)
    total_commitment = sum(s["commitment"] for s in signals)
    lines = [f"📊 إشارات تداول - {today}", market_line, coverage_line,
             f"(مؤشرات فنية من Yahoo، وسعر الإشارة من سهمك؛ جلسة {signals[0]['date']})", ""]
    for s in signals:
        lines += [
            f"🟢 {s['name']} ({s['code']})",
            f"دخول تقريبي: {s['entry']:.2f}",
            f"وقف الخسارة: {s['stop']:.2f}",
            f"الهدف: {s['target']:.2f}",
            f"الكمية المقترحة: {s['shares']} سهم (~{s['value']:,.2f} ريال)",
            f"RSI: {s['rsi']:.0f}",
            f"سعر سهمك محدث: {s['quote_time'].strftime('%H:%M %Z')} ({'متأخر' if s['delayed'] else 'لحظي'})", "",
        ]
    lines += [
        f"إجمالي المخاطرة التقديرية (مع الرسوم والانزلاق): {total_risk:.2f} من 10 ريالات",
        f"إجمالي تكلفة الشراء التقديرية: {total_commitment:.2f} من 1,000 ريال",
        "⚠️ أداة مساعدة فقط وليست نصيحة مالية. القرار والمسؤولية عليك.",
    ]
    return "\n".join(lines)


def send(text: str) -> None:
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        print(text)
        print("\n[تنبيه] ما تم الإرسال: حدد TELEGRAM_TOKEN و TELEGRAM_CHAT_ID")
        return
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=30,
    )
    r.raise_for_status()
    print("تم الإرسال.")


# ===================== الاختبار التاريخي =====================
def net_return_after_costs(entry_fill: float, exit_fill: float) -> float:
    """العائد الصافي بعد رسوم الشراء والبيع؛ الأسعار تشمل الانزلاق بالفعل."""
    if entry_fill <= 0 or exit_fill <= 0:
        raise DataValidationError("سعر دخول أو خروج غير صالح في الاختبار التاريخي.")
    return exit_fill * (1 - FEE) / (entry_fill * (1 + FEE)) - 1


def backtest_symbol(code: str, df: pd.DataFrame | None = None,
                    start=None, end=None) -> list[dict]:
    if df is None:
        raw = fetch(code, "5y")
        validate_session_data(raw, code)
        df = add_indicators(raw).dropna()
    else:
        df = df.copy().sort_index()
    if df.empty:
        raise DataValidationError(f"{code}: لا توجد بيانات مؤشرات للاختبار.")

    start_date = pd.Timestamp(start).date() if start is not None else None
    end_date = pd.Timestamp(end).date() if end is not None else None
    trades, pos = [], None

    for j in range(1, len(df)):
        bar_date = pd.Timestamp(df.index[j]).date()
        if end_date is not None and bar_date > end_date:
            break
        if start_date is not None and bar_date < start_date:
            continue
        prev, bar = df.iloc[j - 1], df.iloc[j]
        if pos is None and buy_signal(prev):
            raw_entry = float(bar.Open)
            raw_stop = float(prev.Close - ATR_STOP_MULT * prev.atr)
            raw_target = float(prev.Close + ATR_TARGET_MULT * prev.atr)
            entry_fill = raw_entry * (1 + SLIPPAGE_RATE)
            stop_fill = raw_stop * (1 - SLIPPAGE_RATE)
            risk_per_share = entry_fill * (1 + FEE) - stop_fill * (1 - FEE)
            if entry_fill > stop_fill > 0 and risk_per_share > 0:
                pos = dict(
                    entry_fill=entry_fill, stop=raw_stop, target=raw_target,
                    stop_fill=stop_fill, risk_per_share=risk_per_share,
                    entry_date=pd.Timestamp(df.index[j]).date(),
                )
        if pos is None:
            continue

        raw_exit = None
        if float(bar.Low) <= pos["stop"]:
            raw_exit = min(float(bar.Open), pos["stop"])
        elif float(bar.High) >= pos["target"]:
            raw_exit = max(float(bar.Open), pos["target"])
        elif bar.ma20 < bar.ma50:
            raw_exit = float(bar.Close)
        if raw_exit is not None:
            exit_fill = raw_exit * (1 - SLIPPAGE_RATE)
            trades.append(dict(
                code=code, entry_date=pos["entry_date"],
                exit_date=bar_date, entry_fill=pos["entry_fill"],
                exit_fill=exit_fill, risk_per_share=pos["risk_per_share"],
                cost_per_share=pos["entry_fill"] * (1 + FEE),
                ret=net_return_after_costs(pos["entry_fill"], exit_fill),
            ))
            pos = None

    if pos is not None:
        eligible = df if end_date is None else df.loc[
            [pd.Timestamp(i).date() <= end_date for i in df.index]
        ]
        if not eligible.empty:
            last = eligible.iloc[-1]
            exit_date = pd.Timestamp(eligible.index[-1]).date()
            exit_fill = float(last.Close) * (1 - SLIPPAGE_RATE)
            trades.append(dict(
                code=code, entry_date=pos["entry_date"], exit_date=exit_date,
                entry_fill=pos["entry_fill"], exit_fill=exit_fill,
                risk_per_share=pos["risk_per_share"],
                cost_per_share=pos["entry_fill"] * (1 + FEE),
                ret=net_return_after_costs(pos["entry_fill"], exit_fill),
            ))
    return trades


def _simulate_portfolio(trades: list[dict]) -> tuple[float, list[dict]]:
    """محاكاة نقدية دون رافعة، بسقف خطر 10 ريالات وتكلفة مراكز 1,000 ريال."""
    cash = float(CAPITAL)
    active = []
    executed = []
    grouped = {}
    for trade in trades:
        grouped.setdefault(trade["entry_date"], []).append(trade)

    for day in sorted(grouped):
        # إقفال أي صفقة كان تاريخ خروجها أسبق من يوم الدخول الجديد.
        still_active = []
        for position in active:
            if position["trade"]["exit_date"] < day:
                cash += position["shares"] * position["trade"]["exit_fill"] * (1 - FEE)
                position["executed"]["profit"] = (
                    position["shares"] * position["trade"]["exit_fill"] * (1 - FEE)
                    - position["shares"] * position["trade"]["cost_per_share"]
                )
            else:
                still_active.append(position)
        active = still_active

        candidates = []
        for trade in sorted(grouped[day], key=lambda item: item["code"]):
            risk_budget = CAPITAL * RISK_PER_TRADE
            position_budget = CAPITAL * MAX_POSITION
            shares = min(
                int(risk_budget / trade["risk_per_share"]),
                int(position_budget / trade["cost_per_share"]),
            )
            if shares > 0:
                candidates.append({
                    "trade": trade, "shares": shares,
                    "risk_per_share": trade["risk_per_share"],
                    "cost_per_share": trade["cost_per_share"],
                })

        used_risk = sum(p["shares"] * p["trade"]["risk_per_share"] for p in active)
        used_cost = sum(p["shares"] * p["trade"]["cost_per_share"] for p in active)
        available_risk = max(0.0, MAX_TOTAL_RISK - used_risk)
        available_cost = max(0.0, MAX_TOTAL_POSITION - used_cost)
        share_counts = _allocate_integer_shares(
            candidates, available_risk, min(available_cost, max(0.0, cash))
        )
        candidates = [
            {"trade": item["trade"], "shares": shares}
            for item, shares in zip(candidates, share_counts) if shares > 0
        ]

        for candidate in candidates:
            trade = candidate["trade"]
            shares = candidate["shares"]
            if shares <= 0:
                continue
            cost = shares * trade["cost_per_share"]
            if cost > cash + 1e-8:
                continue
            cash -= cost
            result = {"code": trade["code"], "shares": shares,
                      "entry_date": trade["entry_date"], "exit_date": trade["exit_date"],
                      "ret": trade["ret"]}
            position = {"trade": trade, "shares": shares, "executed": result}
            active.append(position)
            executed.append(result)

        # إقفال صفقات هذا اليوم بعد السماح لها بالوصول إلى الوقف/الهدف خلال الجلسة.
        still_active = []
        for position in active:
            if position["trade"]["exit_date"] == day:
                proceeds = position["shares"] * position["trade"]["exit_fill"] * (1 - FEE)
                cost = position["shares"] * position["trade"]["cost_per_share"]
                cash += proceeds
                position["executed"]["profit"] = proceeds - cost
            else:
                still_active.append(position)
        active = still_active

    # الصفقات المفتوحة التي لم تمر بتاريخ خروج ضمن أيام دخول أخرى تغلق بسعر خروجها المسجل.
    for position in active:
        proceeds = position["shares"] * position["trade"]["exit_fill"] * (1 - FEE)
        cost = position["shares"] * position["trade"]["cost_per_share"]
        cash += proceeds
        position["executed"]["profit"] = proceeds - cost
    return cash, executed


def _common_period_bounds(datasets: dict[str, pd.DataFrame],
                         index_df: pd.DataFrame):
    """أول وآخر تاريخ موجودين فعليًا في جميع سلاسل الأسهم والمؤشر."""
    if not datasets:
        raise DataValidationError("لا توجد سلاسل أسهم لاشتقاق فترة مشتركة.")
    frames = list(datasets.items()) + [("تاسي", index_df)]
    date_sets = []
    for label, df in frames:
        if df is None or df.empty:
            raise DataValidationError(f"{label}: لا توجد بيانات لتحديد فترة مشتركة.")
        date_sets.append({pd.Timestamp(value).date() for value in df.index})
    shared_dates = set.intersection(*date_sets)
    if len(shared_dates) < 2:
        raise DataValidationError(
            "لا توجد جلستان مشتركتان على الأقل بين جميع الأسهم وتاسي؛ أوقفنا المقارنة."
        )
    ordered = sorted(shared_dates)
    return ordered[0], ordered[-1]


def _benchmark_return(index_df: pd.DataFrame, start, end) -> tuple[float, object, object]:
    start_date = pd.Timestamp(start).date()
    end_date = pd.Timestamp(end).date()
    mask = [(pd.Timestamp(i).date() >= start_date
             and pd.Timestamp(i).date() <= end_date) for i in index_df.index]
    sample = index_df.loc[mask]
    if len(sample) < 2:
        raise DataValidationError("لا تتوفر جلستان على الأقل لتاسي ضمن فترة المقارنة المشتركة.")
    actual_start = pd.Timestamp(sample.index[0]).date()
    actual_end = pd.Timestamp(sample.index[-1]).date()
    if actual_start != start_date or actual_end != end_date:
        raise DataValidationError(
            "بيانات تاسي لا تغطي حدود الفترة المشتركة نفسها؛ أوقفنا المقارنة غير المتكافئة."
        )
    first = float(sample.Close.iloc[0])
    last = float(sample.Close.iloc[-1])
    entry_fill = first * (1 + SLIPPAGE_RATE)
    exit_fill = last * (1 - SLIPPAGE_RATE)
    return net_return_after_costs(entry_fill, exit_fill), \
        actual_start, actual_end


def run_backtest(now: datetime | None = None) -> None:
    datasets = {}
    for code in SYMBOLS:
        raw = fetch(code, "5y")
        validate_session_data(raw, code, now)
        indicators = add_indicators(raw).dropna()
        if indicators.empty or pd.Timestamp(indicators.index[-1]).date() != latest_expected_session(now):
            raise DataValidationError(f"{code}: آخر جلسة لا تحتوي بيانات مؤشرات مكتملة.")
        datasets[code] = indicators

    index_df = fetch_index("5y")
    validate_session_data(index_df, "تاسي", now)
    start, end = _common_period_bounds(datasets, index_df)

    all_trades = []
    for code, df in datasets.items():
        all_trades.extend(backtest_symbol(code, df=df, start=start, end=end))
    final_balance, executed = _simulate_portfolio(all_trades)
    tasi_return, tasi_start, tasi_end = _benchmark_return(index_df, start, end)
    strategy_return = final_balance / CAPITAL - 1
    wins = sum(trade["profit"] > 0 for trade in executed)

    print("===== الاختبار التاريخي (نافذة مشتركة للأسهم وتاسي) =====")
    print(f"الفترة المرجعية: {tasi_start} إلى {tasi_end}")
    print(f"رأس المال الابتدائي: {CAPITAL:,.2f} ريال")
    print(f"صفقات الاستراتيجية المنفذة ضمن سقفي الخطر والمراكز: {len(executed)}")
    if executed:
        print(f"نسبة الصفقات الرابحة: {wins / len(executed):.0%}")
    print(f"رصيد الاستراتيجية النهائي: {final_balance:,.2f} ريال ({strategy_return:+.2%})")
    print(f"تاسي شراء واحتفاظ: {CAPITAL * (1 + tasi_return):,.2f} ريال ({tasi_return:+.2%})")
    print("كلا العائدين بعد عمولة 0.12% وانزلاق افتراضي 0.10% لكل جهة؛ "
          "تاسي مؤشر سعري مرجعي وليس أصلاً قابلاً للتداول مباشرة.")
    print("الأداء السابق لا يضمن النتائج المستقبلية.")


# ===================== التشغيل =====================
def daemon() -> None:
    last_sent = None
    print("البوت يعمل... (Ctrl+C للإيقاف)")
    while True:
        now = datetime.now(TZ)
        due = (now.weekday() in TRADING_DAYS
               and (now.hour, now.minute) >= SEND_TIME
               and last_sent != now.date())
        if due:
            try:
                send(format_message(generate_signals(now)))
                last_sent = now.date()
            except Exception as e:
                # لا نعلّم اليوم كمكتمل؛ يبقى البوت يحاول حتى تتوفر بيانات جلسة موثوقة.
                print(f"[تعذر التحقق أو الإرسال] {e}", file=sys.stderr)
        time.sleep(30)


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "backtest":
        run_backtest()
    elif cmd == "daemon":
        daemon()
    else:
        # الاستثناء قبل send يمنع إرسال «لا توجد فرص» عند فشل التحقق.
        send(format_message(generate_signals()))


if __name__ == "__main__":
    try:
        main()
    except (DataValidationError, SignalAllocationError) as exc:
        print(f"[لم يتم إصدار أو إرسال نتيجة] {exc}", file=sys.stderr)
        sys.exit(2)
