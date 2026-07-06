#!/usr/bin/env python3
"""
EOD Confirm — Stage-2 intraday filter (3:27 PM) on top of the 3:15 scanner shortlist.

Stage 1 (3:15): existing scanner filters → saved to eod_shortlist.json
Stage 2 (3:27): vol >= 1.5x AND (close > open OR near day high OR new high)

Logs every candidate to eod_confirm_log.json.
Confirm-passing signals open positions in paper_portfolio_confirm.json (shadow book).
"""

import json
import os
from datetime import datetime

from angel_api import AngelOneAPI
from long_buildup_scanner import MIN_VOLUME_RATIO, get_avg_volumes_data

SHORTLIST_FILE = "eod_shortlist.json"
CONFIRM_LOG_FILE = "eod_confirm_log.json"
SCAN_FILE = "scan_results.json"
PORTFOLIO_FILE = "paper_portfolio.json"

DAY_RANGE_TOP_PCT = 0.75
NEAR_HIGH_RATIO = 0.995

angel = AngelOneAPI()


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def save_shortlist():
    """Persist today's scanner shortlist after the 3:15 scan."""
    if not os.path.exists(SCAN_FILE):
        log(f"  !! {SCAN_FILE} missing — cannot save shortlist")
        return False

    with open(SCAN_FILE) as f:
        scan = json.load(f)

    today = datetime.now().strftime("%Y-%m-%d")
    payload = {
        "date": today,
        "scan_time": scan.get("scan_time"),
        "macro": scan.get("macro", {}),
        "signals": scan.get("results", []),
    }
    with open(SHORTLIST_FILE, "w") as f:
        json.dump(payload, f, indent=2)
    log(f"  EOD shortlist saved → {SHORTLIST_FILE}  ({len(payload['signals'])} signals)")
    return True


def _load_confirm_log():
    if os.path.exists(CONFIRM_LOG_FILE):
        with open(CONFIRM_LOG_FILE) as f:
            return json.load(f)
    return {"entries": []}


def _save_confirm_log(data):
    with open(CONFIRM_LOG_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _baseline_entered_today(symbol, today):
    if not os.path.exists(PORTFOLIO_FILE):
        return False
    with open(PORTFOLIO_FILE) as f:
        pf = json.load(f)
    pos = pf.get("positions", {}).get(symbol)
    if pos and pos.get("entry_date") == today:
        return True
    for t in pf.get("closed_trades", []):
        if t["symbol"] == symbol and t["entry_date"] == today:
            return True
    return False


def evaluate_intraday(quote, avg_vol, scan_vol_ratio=None):
    """Apply Stage-2 intraday filters."""
    ltp = quote.get("ltp") or 0
    day_open = quote.get("open") or 0
    day_high = quote.get("high") or 0
    day_low = quote.get("low") or 0
    prev_close = quote.get("prev_close") or 0
    volume = quote.get("volume") or 0

    vol_ratio = None
    if avg_vol and avg_vol > 0 and volume > 0:
        vol_ratio = round(volume / avg_vol, 2)

    vol_ok = vol_ratio is not None and vol_ratio >= MIN_VOLUME_RATIO
    close_gt_open = day_open > 0 and ltp > day_open

    near_day_high = False
    if day_high > day_low and ltp > 0:
        range_pos = (ltp - day_low) / (day_high - day_low)
        near_day_high = range_pos >= DAY_RANGE_TOP_PCT or ltp >= day_high * NEAR_HIGH_RATIO

    new_high = day_high > 0 and prev_close > 0 and day_high > prev_close
    strength_ok = close_gt_open or near_day_high or new_high
    confirm_pass = vol_ok and strength_ok

    reasons = []
    if not vol_ok:
        vr = f"{vol_ratio}x" if vol_ratio is not None else "N/A"
        reasons.append(f"vol {vr} < {MIN_VOLUME_RATIO}x")
    if vol_ok and not strength_ok:
        reasons.append("weak close (not green / near high / new high)")

    return {
        "open": round(day_open, 2) if day_open else None,
        "high": round(day_high, 2) if day_high else None,
        "low": round(day_low, 2) if day_low else None,
        "ltp": round(ltp, 2) if ltp else None,
        "vol_ratio_scan": scan_vol_ratio,
        "vol_ratio_confirm": vol_ratio,
        "vol_ok": vol_ok,
        "close_gt_open": close_gt_open,
        "near_day_high": near_day_high,
        "new_high": new_high,
        "confirm_pass": confirm_pass,
        "fail_reason": "; ".join(reasons) if reasons else "",
    }


def run_confirm():
    """Run Stage-2 confirm at 3:27 PM."""
    today = datetime.now().strftime("%Y-%m-%d")
    now_s = datetime.now().strftime("%H:%M:%S")

    if not os.path.exists(SHORTLIST_FILE):
        log(f"  !! {SHORTLIST_FILE} missing — run 3:15 scan first")
        return []

    with open(SHORTLIST_FILE) as f:
        shortlist = json.load(f)

    if shortlist.get("date") != today:
        log(f"  !! Shortlist date {shortlist.get('date')} != today — stale, skipping confirm")
        return []

    signals = shortlist.get("signals", [])
    if not signals:
        log("  No scanner signals in shortlist — nothing to confirm")
        return []

    log(f"  EOD Confirm — evaluating {len(signals)} shortlist candidate(s) @ {now_s}")

    symbols = [s["symbol"] for s in signals]
    quotes = angel.get_quotes(symbols)
    avg_vols = get_avg_volumes_data(symbols)

    confirm_log = _load_confirm_log()
    confirm_log["entries"] = [
        e for e in confirm_log.get("entries", []) if e.get("date") != today
    ]

    passed = []
    macro = shortlist.get("macro", {}).get("sentiment", "UNKNOWN")
    fii_net = float(shortlist.get("macro", {}).get("fii_net_cr", 0) or 0)

    from paper_trader import SECTOR_MAP

    for sig in signals:
        sym = sig["symbol"]
        quote = quotes.get(sym, {})
        ev = evaluate_intraday(quote, avg_vols.get(sym, 0), sig.get("vol_ratio"))
        sec = SECTOR_MAP.get(sym, "-")
        baseline = _baseline_entered_today(sym, today)

        entry = {
            "date": today,
            "symbol": sym,
            "scan_time": shortlist.get("scan_time", ""),
            "confirm_time": now_s,
            "price_chg": sig.get("price_chg"),
            "oi_chg": sig.get("oi_chg"),
            "vol_ratio_scan": sig.get("vol_ratio"),
            "vol_ratio_confirm": ev["vol_ratio_confirm"],
            "pcr": sig.get("pcr"),
            "sector": sec,
            "macro": macro,
            "open": ev["open"],
            "high": ev["high"],
            "low": ev["low"],
            "ltp": ev["ltp"],
            "close_gt_open": ev["close_gt_open"],
            "near_day_high": ev["near_day_high"],
            "new_high": ev["new_high"],
            "confirm_pass": ev["confirm_pass"],
            "baseline_entered": baseline,
            "confirm_entered": False,
            "fail_reason": ev["fail_reason"],
        }

        if ev["confirm_pass"]:
            passed.append({
                **sig,
                "spot_price": ev["ltp"] or sig.get("spot_price"),
                "vol_ratio": ev["vol_ratio_confirm"] or sig.get("vol_ratio"),
            })
            marker = "✓✓"
        else:
            marker = "  "

        log(f"  {marker} {sym:<14} LTP ₹{ev['ltp'] or 0:>8,.2f}"
            f"  Vol {ev['vol_ratio_confirm'] or '-'}x"
            f"  {'PASS' if ev['confirm_pass'] else 'FAIL'}"
            + (f"  ({ev['fail_reason']})" if ev["fail_reason"] else ""))

        confirm_log["entries"].append(entry)

    entered_syms = set()
    if passed:
        import paper_trader
        entered_syms = paper_trader.enter_confirm_positions(passed, macro, fii_net)
        for row in confirm_log["entries"]:
            if row["confirm_pass"] and row["symbol"] in entered_syms:
                row["confirm_entered"] = True

    _save_confirm_log(confirm_log)
    log(f"  Confirm result: {len(passed)}/{len(signals)} passed"
        f"  |  entered: {len(entered_syms)} → {CONFIRM_LOG_FILE}")

    import paper_trader
    paper_trader.refresh_excel()

    return passed


if __name__ == "__main__":
    save_shortlist()
    run_confirm()
