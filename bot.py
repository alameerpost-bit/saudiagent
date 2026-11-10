#!/usr/bin/env python3
"""
بوت إشارات تداول للسوق السعودي (تداول) - أداة دعم قرار وليست نصيحة مالية.

الأوامر:
    python bot.py backtest   # اختبار تاريخي للاستراتيجية
    python bot.py run        # توليد الإشارات وإرسالها مرة وحدة
    python bot.py daemon     # تشغيل مستمر: يرسل كل يوم تداول 9:30 صباحاً (الرياض)
"""
import os
import sys
import time
from datetime import datetime
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
CAPITAL = 1000        # رأس المال بالريال
RISK_PER_TRADE = 0.01    # مخاطرة 1% لكل صفقة
MAX_POSITION = 0.20      # أقصى حجم للمركز الواحد 20% من المحفظة
ATR_STOP_MULT = 2.0      # وقف الخسارة = 2 × ATR
ATR_TARGET_MULT = 3.0    # الهدف = 3 × ATR
FEE = 0.0012             # عمولة + رسوم تقريبية لكل جهة
SEND_TIME = (9, 30)      # وقت الإرسال بتوقيت الرياض
TRADING_DAYS = {6, 0, 1, 2, 3}  # الأحد-الخميس (Python: الإثنين=0 ... الأحد=6)
TZ = ZoneInfo("Asia/Riyadh")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# ===================== البيانات والمؤشرات =====================
def fetch(code: str, period: str = "2y") -> pd.DataFrame:
    df = yf.Ticker(f"{code}.SR").history(period=period, auto_adjust=True)
    return df[["Open", "High", "Low", "Close", "Volume"]].dropna()


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
def position_size(entry: float, stop: float) -> tuple[int, float]:
    risk_amount = CAPITAL * RISK_PER_TRADE
    shares = int(risk_amount / (entry - stop))
    max_shares = int(CAPITAL * MAX_POSITION / entry)
    shares = max(0, min(shares, max_shares))
    return shares, shares * entry


# ===================== الإشارات اليومية =====================
def generate_signals() -> list[dict]:
    out = []
    for code, name in SYMBOLS.items():
        try:
            df = add_indicators(fetch(code, "1y")).dropna()
            r = df.iloc[-1]
            if not buy_signal(r):
                continue
            entry = float(r.Close)
            stop = entry - ATR_STOP_MULT * float(r.atr)
            target = entry + ATR_TARGET_MULT * float(r.atr)
            shares, value = position_size(entry, stop)
            if shares == 0:
                continue
            out.append(dict(code=code, name=name, entry=entry, stop=stop,
                            target=target, shares=shares, value=value,
                            rsi=float(r.rsi), date=df.index[-1].date()))
        except Exception as e:
            print(f"[خطأ] {code}: {e}", file=sys.stderr)
    return out


def format_message(signals: list[dict]) -> str:
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    if not signals:
        return f"📊 إشارات تداول - {today}\nما فيه إشارات شراء اليوم."
    lines = [f"📊 إشارات تداول - {today}", f"(بناءً على إغلاق {signals[0]['date']})", ""]
    for s in signals:
        lines += [
            f"🟢 {s['name']} ({s['code']})",
            f"دخول تقريبي: {s['entry']:.2f}",
            f"وقف الخسارة: {s['stop']:.2f}",
            f"الهدف: {s['target']:.2f}",
            f"الكمية المقترحة: {s['shares']} سهم (~{s['value']:,.0f} ريال)",
            f"RSI: {s['rsi']:.0f}", "",
        ]
    lines.append("⚠️ أداة مساعدة فقط وليست نصيحة مالية. القرار والمسؤولية عليك.")
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
def backtest_symbol(code: str) -> list[dict]:
    df = add_indicators(fetch(code, "5y")).dropna()
    trades, pos = [], None
    for j in range(1, len(df)):
        prev, bar = df.iloc[j - 1], df.iloc[j]
        # دخول بسعر افتتاح اليوم التالي للإشارة
        if pos is None and buy_signal(prev):
            entry = float(bar.Open)
            stop = float(prev.Close - ATR_STOP_MULT * prev.atr)
            target = float(prev.Close + ATR_TARGET_MULT * prev.atr)
            if entry > stop:
                pos = dict(entry=entry, stop=stop, target=target,
                           date=df.index[j])
        if pos is None:
            continue
        exit_price = None
        if bar.Low <= pos["stop"]:
            exit_price = min(float(bar.Open), pos["stop"])
        elif bar.High >= pos["target"]:
            exit_price = max(float(bar.Open), pos["target"])
        elif bar.ma20 < bar.ma50:
            exit_price = float(bar.Close)
        if exit_price is not None:
            ret = exit_price / pos["entry"] - 1 - 2 * FEE
            frac = min(RISK_PER_TRADE / ((pos["entry"] - pos["stop"]) / pos["entry"]),
                       MAX_POSITION)
            trades.append(dict(code=code, exit=df.index[j], ret=ret, frac=frac))
            pos = None
    return trades


def run_backtest() -> None:
    all_trades = []
    for code, name in SYMBOLS.items():
        try:
            t = backtest_symbol(code)
        except Exception as e:
            print(f"[خطأ] {code}: {e}", file=sys.stderr)
            continue
        all_trades += t
        if t:
            wins = sum(x["ret"] > 0 for x in t)
            print(f"{name:10s} صفقات: {len(t):3d}  نسبة الربح: {wins / len(t):.0%}")
    if not all_trades:
        print("ما فيه صفقات.")
        return
    all_trades.sort(key=lambda x: x["exit"])
    equity, peak, max_dd = 1.0, 1.0, 0.0
    for t in all_trades:
        equity *= 1 + t["frac"] * t["ret"]
        peak = max(peak, equity)
        max_dd = max(max_dd, 1 - equity / peak)
    wins = [t["ret"] for t in all_trades if t["ret"] > 0]
    losses = [t["ret"] for t in all_trades if t["ret"] <= 0]
    print("\n===== النتيجة الإجمالية (5 سنوات) =====")
    print(f"عدد الصفقات: {len(all_trades)}")
    print(f"نسبة الربح: {len(wins) / len(all_trades):.0%}")
    print(f"متوسط الربح: {sum(wins) / max(1, len(wins)):.2%} | "
          f"متوسط الخسارة: {sum(losses) / max(1, len(losses)):.2%}")
    print(f"العائد الكلي للمحفظة: {equity - 1:.1%}")
    print(f"أقصى تراجع: {max_dd:.1%}")
    print("\nتذكير: الأداء السابق لا يضمن المستقبل. لا تشغّل الاستراتيجية بمال حقيقي "
          "إلا إذا النتائج مقنعة بعد خصم العمولات.")


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
                send(format_message(generate_signals()))
                last_sent = now.date()
            except Exception as e:
                print(f"[خطأ إرسال] {e}", file=sys.stderr)
        time.sleep(30)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "backtest":
        run_backtest()
    elif cmd == "daemon":
        daemon()
    else:
        send(format_message(generate_signals()))
