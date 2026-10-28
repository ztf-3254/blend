#!/usr/bin/env python3
"""Blend Signal in the cloud (GitHub Actions) + the data for the dashboard (GitHub Pages).

The rule (unchanged): the sleeve holds the 3x ETF while SPY's daily close is above its 200-day simple moving average,
otherwise cash (a T-bill ETF); the core S&P 500 ETF is always held; rebalance to 50/50 at the start of each quarter.

Runs after each US close. Sends a Telegram message only when the sleeve should switch, on the first trading day of each
quarter, and once when it starts. Writes docs/data.json for the dashboard (market data only, no holdings) and keeps its
memory in state.json (committed back to the repo by the workflow).

  python blend_cloud.py            the daily check (what the schedule runs)
  python blend_cloud.py --force    check and report even if today was already checked
  python blend_cloud.py --test     send a Telegram test message
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
HERE = Path(__file__).resolve().parent
STATE = HERE / "state.json"
DATA = HERE / "docs" / "data.json"
DEFAULTS = {"index": "SPY", "sma_days": 200, "core": "VOO", "sleeve": "UPRO", "cash": "SGOV", "split": "50/50"}


def config():
    c = dict(DEFAULTS)
    try:
        c.update(json.loads((HERE / "config.json").read_text()))
    except Exception:
        pass
    return c


def log(msg):
    print("%s %s" % (dt.datetime.now(NY).strftime("%Y-%m-%d %H:%M ET"), msg), flush=True)


def send(text):
    token, chat = os.environ.get("BLEND_TELEGRAM_BOT_TOKEN"), os.environ.get("BLEND_TELEGRAM_CHAT_ID")
    if not (token and chat):
        log("Telegram secrets missing (BLEND_TELEGRAM_BOT_TOKEN / BLEND_TELEGRAM_CHAT_ID); message was:\n" + text)
        return False
    data = urllib.parse.urlencode({"chat_id": chat, "text": text, "disable_web_page_preview": "true"}).encode()
    for _ in range(3):
        try:
            with urllib.request.urlopen("https://api.telegram.org/bot%s/sendMessage" % token, data=data, timeout=30) as r:
                if json.loads(r.read().decode()).get("ok"):
                    return True
        except Exception as e:
            log("Telegram send failed (%s), retrying" % e)
            time.sleep(5)
    return False


def daily(sym, days=800):
    """[(date, adjusted close)] from Yahoo (dividends reinvested), oldest first."""
    p1 = int((dt.datetime.now(NY) - dt.timedelta(days=days)).timestamp())
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?%s" % (
        urllib.parse.quote(sym), urllib.parse.urlencode({"interval": "1d", "period1": p1, "period2": int(time.time())}))
    last = None
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Blend-Signal"})
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.loads(r.read().decode())["chart"]["result"][0]
            adj = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose") or res["indicators"]["quote"][0]["close"]
            raw = res["indicators"]["quote"][0]["close"]
            out = [(dt.datetime.fromtimestamp(t, NY).date().isoformat(), float(a), float(c))
                   for t, a, c in zip(res["timestamp"], adj, raw) if a and c]
            if out:
                return out
        except Exception as e:
            last = e
            time.sleep(5)
    raise RuntimeError("Yahoo: %s" % last)


def sma(vals, n):
    out, s = [], 0.0
    for i, v in enumerate(vals):
        s += v
        if i >= n:
            s -= vals[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def next_quarter_start(day):
    d = dt.date.fromisoformat(day)
    m = ((d.month - 1) // 3 + 1) * 3 + 1
    y = d.year + (m > 12)
    m = m - 12 if m > 12 else m
    q = dt.date(y, m, 1)
    while q.weekday() >= 5 or (q.month == 1 and q.day == 1):  # weekends and New Year's Day
        q += dt.timedelta(days=1)
    return q.isoformat()


def build(c, rows):
    """Everything the dashboard shows, from SPY's daily closes (market data only)."""
    dates = [r[0] for r in rows]
    closes = [r[1] for r in rows]  # adjusted, the same kind of prices as the backtest
    avg = sma(closes, int(c["sma_days"]))
    i = len(rows) - 1
    above = closes[i] > avg[i]
    # switch history: every day the close crossed its average (the trade is at the next open)
    hist, prev = [], None
    for k in range(len(rows)):
        if avg[k] is None:
            continue
        s = closes[k] > avg[k]
        if prev is not None and s != prev:
            hist.append({"date": dates[k], "to": "in" if s else "out", "close": round(closes[k], 2), "avg": round(avg[k], 2)})
        prev = s
    since = hist[-1]["date"] if hist else None
    chart = [{"d": dates[k], "c": round(closes[k], 2), "a": round(avg[k], 2)} for k in range(max(0, i - 260), i + 1) if avg[k]]
    return {"date": dates[i], "close": round(rows[i][2], 2), "adj_close": round(closes[i], 2), "avg": round(avg[i], 2),
            "distance_pct": round((closes[i] / avg[i] - 1) * 100, 2), "above": above,
            "sleeve_should_hold": c["sleeve"] if above else c["cash"], "since": since,
            "history": list(reversed(hist[-12:])), "chart": chart, "next_rebalance": next_quarter_start(dates[i])}


def quarter_start(rows):
    return len(rows) > 1 and rows[-1][0][5:7] in ("01", "04", "07", "10") and rows[-1][0][5:7] != rows[-2][0][5:7]


def run(force=False):
    c = config()
    rows = daily(c["index"])
    view = build(c, rows)
    prices = {}
    for sym in (c["core"], c["sleeve"], c["cash"]):
        try:
            prices[sym] = round(daily(sym, 10)[-1][2], 2)
        except Exception:
            prices[sym] = None
    today = dt.datetime.now(NY).date().isoformat()
    st = load_state()
    want = "in" if view["above"] else "out"
    hold = view["sleeve_should_hold"]
    desc = "%s closed at $%.2f, %s its %d-day average (%+.1f%%)." % (
        c["index"], view["close"], "above" if view["above"] else "below", c["sma_days"], view["distance_pct"])
    if view["date"] != today and not force:
        log("No close for today yet (latest %s): holiday or data not final." % view["date"])
    elif st.get("last_date") == view["date"] and not force:
        log("Already checked %s." % view["date"])
    else:
        if not st.get("position"):
            send("Blend Signal is now running in the cloud.\n\n%s\n\nRight now the sleeve should hold: %s.\n\nYou'll get a "
                 "message only when that changes, plus a reminder at the start of each quarter to rebalance to %s."
                 % (desc, hold, c["split"]))
        elif want != st["position"]:
            if want == "in":
                send("🟢 Blend Signal: switch the sleeve INTO %s\n\n%s\n\nAt tomorrow's open: sell the %s in the sleeve and "
                     "buy %s. Leave the core %s as it is." % (c["sleeve"], desc, c["cash"], c["sleeve"], c["core"]))
            else:
                send("🔴 Blend Signal: switch the sleeve to %s\n\n%s\n\nAt tomorrow's open: sell the %s in the sleeve and buy "
                     "%s. Leave the core %s as it is." % (c["cash"], desc, c["sleeve"], c["cash"], c["core"]))
            log("Switch: %s -> %s" % (st["position"], want))
        elif force:
            send("Blend Signal status\n\n%s\nThe sleeve should hold: %s." % (desc, hold))
        else:
            log("No change: sleeve stays %s (%+.1f%%)" % (want, view["distance_pct"]))
        q = "%s-Q%d" % (view["date"][:4], (int(view["date"][5:7]) - 1) // 3 + 1)
        if quarter_start(rows) and st.get("last_rebalance") != q:
            send("🔁 Blend Signal: quarterly rebalance\n\nBring the core (%s) and the sleeve back to %s of the total. The "
                 "sleeve currently holds: %s." % (c["core"], c["split"], hold))
            st["last_rebalance"] = q
        st.update(position=want, last_date=view["date"], checked=dt.datetime.now(NY).isoformat(timespec="minutes"))
        STATE.write_text(json.dumps(st, indent=1) + "\n")
    view.update(updated=dt.datetime.now(NY).isoformat(timespec="minutes"), prices=prices, names=c,
                last_rebalance=st.get("last_rebalance"))
    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps(view, separators=(",", ":")))
    log("%s Sleeve should hold %s. Dashboard data written." % (desc, hold))


if __name__ == "__main__":
    if "--test" in sys.argv:
        sys.exit(0 if send("Blend Signal test message from the cloud: Telegram is set up.") else 1)
    run(force="--force" in sys.argv)
