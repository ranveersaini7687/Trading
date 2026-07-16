#!/usr/bin/env python3
"""
Run the full EOD scheduler flow locally (bypasses market-hours gate).

Mirrors run_bot.py:
  3:25  scanner → paper_trader → save shortlist
  3:30  eod_confirm → shadow entries → Excel refresh

Usage:
  python3 scripts/run_local_eod_cycle.py
  python3 scripts/run_local_eod_cycle.py --scan-only
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)


def _load_env():
    env_path = os.path.join(ROOT, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _validate_scan_output():
    path = os.path.join(ROOT, "scan_results.json")
    if not os.path.exists(path):
        print("  !! scan_results.json missing")
        return False
    with open(path) as f:
        data = json.load(f)

    crit = data.get("criteria", {})
    ok = True
    checks = [
        ("criteria.min_pcr", crit.get("min_pcr") == 0.7),
        ("criteria.min_volume_ratio", crit.get("min_volume_ratio") == 1.2),
        ("nifty block", "nifty" in data or data.get("macro", {}).get("nifty_chg_pct") is not None),
    ]
    for label, passed in checks:
        status = "OK" if passed else "MISSING"
        print(f"    [{status}] {label}")
        ok = ok and passed

    results = data.get("results", [])
    shadow = data.get("shadow_scores", [])
    print(f"    matched={len(results)}  shadow_scored={len(shadow)}")

    sample = results[0] if results else (shadow[0] if shadow else None)
    if sample:
        model_keys = ["model_a", "model_f", "composite_abc", "ce_oi", "suggested_action", "nifty_chg_pct"]
        for k in model_keys:
            has = k in sample
            print(f"    [{'OK' if has else 'MISSING'}] results[].{k} = {sample.get(k, '-')}")
            ok = ok and has
    else:
        print("    (no matched/shadow signals to inspect model fields)")

    return ok


def _validate_portfolio():
    path = os.path.join(ROOT, "paper_portfolio.json")
    if not os.path.exists(path):
        return
    with open(path) as f:
        pf = json.load(f)
    today = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
    new_today = [
        sym for sym, p in pf.get("positions", {}).items()
        if p.get("entry_date") == today and p.get("model_a") is not None
    ]
    if new_today:
        sym = new_today[0]
        p = pf["positions"][sym]
        print(f"    new entry today with models: {sym}  model_a={p.get('model_a')}  liquid ce_oi={p.get('ce_oi')}")


def main():
    _load_env()
    scan_only = "--scan-only" in sys.argv

    from run_bot import run_eod_scan, run_eod_confirm

    print("=" * 72)
    print("  LOCAL EOD CYCLE (scheduler flow — market hours bypassed)")
    print("=" * 72)

    print("\n── Phase 1: EOD Scan (3:25) ──")
    run_eod_scan()

    print("\n── Validate scan_results.json (Phase 1 fields) ──")
    _validate_scan_output()

    if scan_only:
        print("\n  --scan-only: skipping confirm phase")
        return

    print("\n── Phase 2: EOD Confirm (3:30) — no wait, immediate ──")
    run_eod_confirm(notify=False)

    print("\n── Validate portfolio / Excel ──")
    _validate_portfolio()
    if os.path.exists(os.path.join(ROOT, "trade_log.xlsx")):
        print("    trade_log.xlsx updated")

    print("\n── Done. Check scan_results.json, eod_shortlist.json, trade_log.xlsx ──")


if __name__ == "__main__":
    main()
