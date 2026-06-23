#!/usr/bin/env python3
"""
Bot Runner — Auto-schedules scanner + paper trader during NSE market hours.

Market hours    : 9:15 AM – 3:30 PM IST, Monday–Friday
Scan interval   : every 5 minutes during market hours
Post-market scan: starts at 4:40 PM IST, retries every 5 min until bhav copy fetched

Usage:
  python3 run_bot.py          # runs forever, Ctrl+C to stop
  python3 run_bot.py --once   # single cycle (for cron / testing)
"""

import os
import sys
import time
import traceback
from datetime import datetime, timedelta

MARKET_OPEN_H,  MARKET_OPEN_M  =  9, 15
MARKET_CLOSE_H, MARKET_CLOSE_M = 15, 30
EOD_SCAN_H,     EOD_SCAN_M     = 15, 15   # 3:15 PM — full scan with live data
SCAN_INTERVAL_SEC = 300   # 5 minutes


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def is_market_open():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    open_t  = MARKET_OPEN_H  * 60 + MARKET_OPEN_M
    close_t = MARKET_CLOSE_H * 60 + MARKET_CLOSE_M
    return open_t <= t <= close_t


def is_eod_scan_time():
    """True from 3:15 PM onwards during market hours on weekdays."""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    return t >= EOD_SCAN_H * 60 + EOD_SCAN_M


def seconds_until_open():
    now = datetime.now()
    nxt = now.replace(hour=MARKET_OPEN_H, minute=MARKET_OPEN_M, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return max(0, int((nxt - now).total_seconds()))


def run_intraday_check():
    """Every 5-min intraday: only check SL/target on open positions."""
    import importlib
    import paper_trader
    importlib.reload(paper_trader)

    log("── SL/Target Check ──────────────────────────")
    paper_trader.run(intraday_only=True)
    log("── Cycle complete ───────────────────────────")


def run_cycle(notify=False):
    """Post-market: full scanner + enter positions + optional WhatsApp."""
    import importlib
    import long_buildup_scanner
    import paper_trader
    import run_once
    importlib.reload(long_buildup_scanner)
    importlib.reload(paper_trader)
    importlib.reload(run_once)

    log("── Scanner ──────────────────────────────────")
    long_buildup_scanner.scan()
    log("── Paper Trader ─────────────────────────────")
    paper_trader.run()
    if notify:
        log("── Sending WhatsApp summary ─────────────────")
        run_once.send_whatsapp(run_once.build_summary())
    log("── Cycle complete ───────────────────────────")


def main():
    once = "--once" in sys.argv

    log("=" * 60)
    log("  AUTO TRADER BOT — Starting up")
    log(f"  Market hours : {MARKET_OPEN_H:02d}:{MARKET_OPEN_M:02d} – "
        f"{MARKET_CLOSE_H:02d}:{MARKET_CLOSE_M:02d} IST  Mon–Fri")
    log(f"  EOD scan     : {EOD_SCAN_H:02d}:{EOD_SCAN_M:02d} IST (Angel One live data, enter positions)")
    log(f"  Scan interval: every {SCAN_INTERVAL_SEC // 60} minutes (SL/target check)")
    log("  Press Ctrl+C to stop")
    log("=" * 60)

    eod_scan_done_date = None

    while True:
        today = datetime.now().strftime("%Y-%m-%d")

        if is_market_open():
            if is_eod_scan_time() and eod_scan_done_date != today:
                # ── 3:15 PM: full scan + enter positions + WhatsApp ─
                log(f"  EOD scan triggered ({EOD_SCAN_H:02d}:{EOD_SCAN_M:02d}) — running full cycle...")
                try:
                    run_cycle(notify=True)
                    eod_scan_done_date = today
                    log("  EOD scan complete ✓")
                except KeyboardInterrupt:
                    raise
                except Exception:
                    log("  !! EOD scan failed:")
                    traceback.print_exc()
            else:
                # ── Before 3:15 PM: SL/target check only ───────────
                try:
                    run_intraday_check()
                except KeyboardInterrupt:
                    raise
                except Exception:
                    log("  !! Intraday check error:")
                    traceback.print_exc()

            if once:
                log("  --once flag set, exiting after one cycle.")
                break

            log(f"  Next check in {SCAN_INTERVAL_SEC // 60} min "
                f"(~{(datetime.now() + timedelta(seconds=SCAN_INTERVAL_SEC)).strftime('%H:%M')})")
            time.sleep(SCAN_INTERVAL_SEC)

        else:
            # ── Sleep until market open ────────────────────────────
            now  = datetime.now()
            secs = seconds_until_open()
            h, m = divmod(secs // 60, 60)

            if now.weekday() >= 5:
                reason = f"Weekend ({['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][now.weekday()]})"
            elif now.hour * 60 + now.minute < MARKET_OPEN_H * 60 + MARKET_OPEN_M:
                reason = "Pre-market"
            else:
                reason = "Market closed"

            log(f"  {reason} — next open in {h}h {m}m  (sleeping 10 min)")

            if once:
                log("  Market closed and --once flag set, exiting.")
                break

            for _ in range(60):
                time.sleep(10)
                if is_market_open():
                    break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("\n  Bot stopped by user. Goodbye.")
