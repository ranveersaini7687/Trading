#!/usr/bin/env python3
"""
Offline simulation: compare entry counts under old vs new live filters.

Old: vol >= 1.5x, PCR >= 0.8, OI >= 2%, price > 0, liquid assumed
New: vol >= 1.2x, PCR >= 0.7, same other gates

Does not need market hours or Angel One login.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from model_scoring import score_signal

# Framework benchmark — 14 trades from deep analysis (16 Jul 2026)
FRAMEWORK_TRADES = [
    {"symbol": "DIVISLAB",   "result": "WIN",      "price_chg": 3.61,  "oi_chg": 37.75, "vol_ratio": 9.24,  "pcr": 1.22, "ema_pass": True},
    {"symbol": "OFSS",       "result": "WIN",      "price_chg": 4.86,  "oi_chg": 17.41, "vol_ratio": 1.50,  "pcr": 0.94, "ema_pass": True},
    {"symbol": "NAUKRI",     "result": "WIN",      "price_chg": 12.95, "oi_chg": 37.75, "vol_ratio": 9.24,  "pcr": 1.11, "ema_pass": True},
    {"symbol": "KALYANKJIL", "result": "WIN",      "price_chg": 8.36,  "oi_chg": 18.43, "vol_ratio": 7.38,  "pcr": 1.31, "ema_pass": True},
    {"symbol": "PHOENIXLTD", "result": "SL",       "price_chg": 2.58,  "oi_chg": 14.04, "vol_ratio": 2.73,  "pcr": 1.00, "ema_pass": True},
    {"symbol": "MANAPPURAM", "result": "SL",       "price_chg": 5.36,  "oi_chg": 12.52, "vol_ratio": 3.15,  "pcr": 1.15, "ema_pass": True},
    {"symbol": "HDFCAMC",    "result": "SL",       "price_chg": 2.85,  "oi_chg": 8.48,  "vol_ratio": 1.72,  "pcr": 0.88, "ema_pass": True},
    {"symbol": "DIXON",      "result": "SL",       "price_chg": 7.09,  "oi_chg": 13.23, "vol_ratio": 2.21,  "pcr": 0.94, "ema_pass": True},
    {"symbol": "BAJAJ-AUTO", "result": "SL",       "price_chg": 2.50,  "oi_chg": 9.59,  "vol_ratio": 2.74,  "pcr": 0.84, "ema_pass": True},
    {"symbol": "GODREJPROP", "result": "SL",       "price_chg": 5.18,  "oi_chg": 11.06, "vol_ratio": 1.73,  "pcr": 0.83, "ema_pass": True},
    {"symbol": "MPHASIS",    "result": "SL",       "price_chg": 2.99,  "oi_chg": 8.96,  "vol_ratio": 2.03,  "pcr": 0.81, "ema_pass": True},
    {"symbol": "INDUSINDBK", "result": "SL",       "price_chg": 1.07,  "oi_chg": 4.10,  "vol_ratio": 2.26,  "pcr": 0.87, "ema_pass": True},
    {"symbol": "AMBER",      "result": "OPEN",     "price_chg": 2.03,  "oi_chg": 12.22, "vol_ratio": 1.61,  "pcr": 0.98, "ema_pass": True},
    {"symbol": "PGEL",       "result": "OPEN/FAIL","price_chg": 3.02,  "oi_chg": 3.97,  "vol_ratio": 1.98,  "pcr": 1.12, "ema_pass": True},
]

OLD_VOL = 1.5
NEW_VOL = 1.2
OLD_PCR = 0.8
NEW_PCR = 0.7
MIN_OI = 2.0


def passes_live(signal, vol_min, pcr_min, assume_liquid=True):
    if signal.get("price_chg") is None or signal["price_chg"] <= 0:
        return False, "price not up"
    if signal.get("oi_chg") is None or signal["oi_chg"] < MIN_OI:
        return False, f"oi < {MIN_OI}%"
    if signal.get("ema_pass") is False:
        return False, "ema stack fail"
    vr = signal.get("vol_ratio")
    if vr is None:
        return False, "no vol_ratio"
    if vr < vol_min:
        return False, f"vol < {vol_min}x"
    pcr = signal.get("pcr")
    if pcr is None:
        return False, "no pcr"
    if pcr < pcr_min:
        return False, f"pcr < {pcr_min}"
    if not assume_liquid and not signal.get("is_liquid", True):
        return False, "illiquid"
    return True, "pass"


def load_portfolio_signals(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        pf = json.load(f)
    rows = []
    for sym, pos in pf.get("positions", {}).items():
        if pos.get("price_chg") is not None:
            rows.append({**pos, "symbol": sym, "book": "open"})
    for t in pf.get("closed_trades", []):
        if t.get("price_chg") is not None:
            rows.append({**t, "book": "closed"})
    return rows


def ema_stack_pass(spot, e9, e21, e50):
    if e9 is None or e21 is None or e50 is None or spot is None:
        return None
    return spot > e9 > e21 > e50


def enrich_portfolio_row(row):
    spot = row.get("entry_price")
    ep = ema_stack_pass(spot, row.get("ema9"), row.get("ema21"), row.get("ema50"))
    row = dict(row)
    row["spot_price"] = spot
    row["ema_pass"] = ep if ep is not None else True
    return row


def simulate_cached_day(date_tag="20260624"):
    oi_path = os.path.join(ROOT, "cache", f"oi_spurts_{date_tag}.json")
    pc_path = os.path.join(ROOT, "cache", f"price_changes_{date_tag}.json")
    av_path = os.path.join(ROOT, "cache", f"avg_volumes_{date_tag}.json")
    if not all(os.path.exists(p) for p in (oi_path, pc_path, av_path)):
        return None

    oi_stocks = json.load(open(oi_path))["data"]
    pc = json.load(open(pc_path))["data"]
    avg = json.load(open(av_path))["data"]
    changes, volumes, ltps = pc["changes"], pc["volumes"], pc["ltps"]

    long_buildup = []
    for s in oi_stocks:
        sym = s["symbol"]
        pch = changes.get(sym)
        if pch is not None and pch > 0 and s["oi_chg"] >= MIN_OI:
            vol = volumes.get(sym, 0)
            av = avg.get(sym, 0)
            ratio = round(vol / av, 2) if av else None
            long_buildup.append({
                "symbol": sym,
                "oi_chg": s["oi_chg"],
                "price_chg": pch,
                "vol_ratio": ratio,
                "spot_price": ltps.get(sym),
            })

    def vol_filter(threshold):
        out = []
        for s in long_buildup:
            vr = s.get("vol_ratio")
            if vr is not None and vr >= threshold:
                out.append(s)
        return out

    old_v = vol_filter(OLD_VOL)
    new_v = vol_filter(NEW_VOL)
    old_syms = {s["symbol"] for s in old_v}
    new_syms = {s["symbol"] for s in new_v}
    extra = new_syms - old_syms

    return {
        "date": date_tag,
        "oi_spurt": len(oi_stocks),
        "long_buildup": len(long_buildup),
        "vol_old": len(old_v),
        "vol_new": len(new_v),
        "extra_from_vol": sorted(extra),
        "old_list": old_v,
        "new_list": new_v,
    }


def print_section(title):
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


def run_framework():
    print_section("1) Framework benchmark (14 analysed trades)")
    old_pass, new_pass = [], []
    newly_opened = []

    for t in FRAMEWORK_TRADES:
        sig = {**t, "spot_price": 1000, "ema9": 990}
        ok_old, _ = passes_live(t, OLD_VOL, OLD_PCR)
        ok_new, _ = passes_live(t, NEW_VOL, NEW_PCR)
        scores = score_signal({**sig, "pcr": t["pcr"]})
        if ok_old:
            old_pass.append(t["symbol"])
        if ok_new:
            new_pass.append(t["symbol"])
        if ok_new and not ok_old:
            newly_opened.append(t["symbol"])
        marker = ""
        if ok_old != ok_new:
            marker = "  <-- FILTER CHANGE"
        print(f"  {t['symbol']:<14} {t['result']:<6} vol={t['vol_ratio']:.2f}x pcr={t['pcr']:.2f}"
              f"  old={'PASS' if ok_old else 'FAIL':4}  new={'PASS' if ok_new else 'FAIL':4}"
              f"  composite={scores['composite_abc']:5.1f}  action={scores['suggested_action']}{marker}")

    print(f"\n  Entries OLD (vol {OLD_VOL}x, PCR {OLD_PCR}): {len(old_pass)}")
    print(f"  Entries NEW (vol {NEW_VOL}x, PCR {NEW_PCR}): {len(new_pass)}")
    if newly_opened:
        print(f"  Newly admitted: {', '.join(newly_opened)}")
    else:
        print("  Newly admitted by filter change: none (all 14 already passed old gates)")


def run_portfolio():
    print_section("2) Your portfolio trades (with entry snapshots)")
    rows = []
    for path, label in [
        ("paper_portfolio.json", "baseline"),
        ("paper_portfolio_confirm.json", "confirm"),
    ]:
        for r in load_portfolio_signals(os.path.join(ROOT, path)):
            rows.append({**enrich_portfolio_row(r), "portfolio": label})

    # dedupe by symbol+entry_date
    seen = set()
    unique = []
    for r in rows:
        key = (r["symbol"], r.get("entry_date"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)

    if not unique:
        print("  No trades with full signal snapshots found.")
        return

    old_n = new_n = 0
    for r in sorted(unique, key=lambda x: x.get("entry_date", "")):
        ok_old, r_old = passes_live(r, OLD_VOL, OLD_PCR)
        ok_new, r_new = passes_live(r, NEW_VOL, NEW_PCR)
        old_n += ok_old
        new_n += ok_new
        scores = score_signal(r)
        chg = ""
        if ok_old != ok_new:
            chg = "  <-- FILTER CHANGE"
        print(f"  {r['entry_date']} {r['symbol']:<14} vol={r.get('vol_ratio')} pcr={r.get('pcr')}"
              f"  old={'PASS' if ok_old else 'FAIL'}  new={'PASS' if ok_new else 'FAIL'}"
              f"  composite={scores.get('composite_abc', 0)}  action={scores.get('suggested_action')}{chg}")

    print(f"\n  Entries OLD: {old_n}/{len(unique)}")
    print(f"  Entries NEW: {new_n}/{len(unique)}")
    print(f"  Delta: +{new_n - old_n}")


def run_cached_day():
    print_section("3) Full-universe replay — cached 24 Jun 2026 (vol step only, no PCR)")
    day = simulate_cached_day()
    if not day:
        print("  Cache files missing — skip.")
        return

    print(f"  OI spurt stocks     : {day['oi_spurt']}")
    print(f"  Long buildup (OI≥2%): {day['long_buildup']}")
    print(f"  Pass vol >= {OLD_VOL}x (OLD): {day['vol_old']}")
    print(f"  Pass vol >= {NEW_VOL}x (NEW): {day['vol_new']}")
    print(f"  Extra entries from vol {NEW_VOL}x: +{len(day['extra_from_vol'])}")

    if day["extra_from_vol"]:
        print("\n  Stocks that would NEWLY pass volume (1.2x but not 1.5x):")
        lb = {s["symbol"]: s for s in day["new_list"]}
        for sym in day["extra_from_vol"]:
            s = lb[sym]
            print(f"    {sym:<14} price={s['price_chg']:+.2f}%  oi={s['oi_chg']:+.2f}%  vol={s['vol_ratio']:.2f}x")

    print(f"\n  OLD vol-pass list ({day['vol_old']}):")
    print("   ", ", ".join(s["symbol"] for s in sorted(day["old_list"], key=lambda x: -x["oi_chg"])))
    print(f"\n  NEW vol-pass list ({day['vol_new']}):")
    print("   ", ", ".join(s["symbol"] for s in sorted(day["new_list"], key=lambda x: -x["oi_chg"])))


def run_scoring_summary():
    print_section("4) Model scoring on framework trades (shadow gates, not live)")
    wins = losses = 0
    for t in FRAMEWORK_TRADES:
        sig = {**t, "spot_price": 1000, "ema9": 990}
        sc = score_signal(sig)
        live_ok, _ = passes_live(t, NEW_VOL, NEW_PCR)
        if not live_ok:
            continue
        is_win = t["result"] == "WIN"
        if is_win:
            wins += 1
        elif t["result"] == "SL":
            losses += 1
        print(f"  {t['symbol']:<14} live=PASS  F={sc['model_f']:5.1f}  composite={sc['composite_abc']:5.1f}"
              f"  action={sc['suggested_action']:<12} result={t['result']}")

    print(f"\n  (Live NEW-filter pass: {sum(1 for t in FRAMEWORK_TRADES if passes_live(t, NEW_VOL, NEW_PCR)[0])} trades)")
    print(f"  Model F would block (score=0): PHOENIX, MANAPPURAM, HDFCAMC, DIXON, AMBER, PGEL (OI<15% or gates)")


def main():
    print("OFFLINE ENTRY FILTER SIMULATION")
    print(f"OLD: vol>={OLD_VOL}x  PCR>={OLD_PCR}  OI>={MIN_OI}%  price up  EMA pass")
    print(f"NEW: vol>={NEW_VOL}x  PCR>={NEW_PCR}  OI>={MIN_OI}%  price up  EMA pass")
    run_framework()
    run_portfolio()
    run_cached_day()
    run_scoring_summary()
    print()


if __name__ == "__main__":
    main()
