#!/usr/bin/env python3
"""
Bot Runner — Auto-schedules scanner + paper trader during NSE market hours.

Market hours : 9:15 AM – 3:30 PM IST, Monday–Friday
Scan interval: every 5 minutes during market hours

Usage:
  python3 run_bot.py          # runs forever, Ctrl+C to stop
  python3 run_bot.py --once   # single cycle (for cron / testing)
"""

import sys
import time
import traceback
from datetime import datetime, timedelta

MARKET_OPEN_H,  MARKET_OPEN_M  =  9, 15
MARKET_CLOSE_H, MARKET_CLOSE_M = 15, 30
SCAN_INTERVAL_SEC = 300   # 5 minutes


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def is_market_open():
    now = datetime.now()
    if now.weekday() >= 5:          # Saturday=5, Sunday=6
        return False
    t = now.hour * 60 + now.minute
    open_t  = MARKET_OPEN_H  * 60 + MARKET_OPEN_M
    close_t = MARKET_CLOSE_H * 60 + MARKET_CLOSE_M
    return open_t <= t <= close_t


def seconds_until_open():
    now  = datetime.now()
    # Next weekday open
    nxt  = now.replace(hour=MARKET_OPEN_H, minute=MARKET_OPEN_M, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return max(0, int((nxt - now).total_seconds()))


def run_cycle():
    # Import here so each cycle picks up any code changes without restarting
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
    log("── Sending WhatsApp summary ─────────────────")
    run_once.send_whatsapp(run_once.build_summary())
    log("── Cycle complete ───────────────────────────")


def main():
    once = "--once" in sys.argv
    log("=" * 60)
    log("  AUTO TRADER BOT — Starting up")
    log(f"  Market hours : {MARKET_OPEN_H:02d}:{MARKET_OPEN_M:02d} – "
        f"{MARKET_CLOSE_H:02d}:{MARKET_CLOSE_M:02d} IST  Mon–Fri")
    log(f"  Scan interval: every {SCAN_INTERVAL_SEC // 60} minutes")
    log("  Press Ctrl+C to stop")
    log("=" * 60)

    while True:
        if is_market_open():
            try:
                run_cycle()
            except KeyboardInterrupt:
                raise
            except Exception:
                log("  !! Cycle error (will retry next interval):")
                traceback.print_exc()

            if once:
                log("  --once flag set, exiting after one cycle.")
                break

            log(f"  Next scan in {SCAN_INTERVAL_SEC // 60} min "
                f"(~{(datetime.now() + timedelta(seconds=SCAN_INTERVAL_SEC)).strftime('%H:%M')})")
            time.sleep(SCAN_INTERVAL_SEC)

        else:
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

            # Sleep in 10-min chunks so Ctrl+C is responsive
            for _ in range(60):
                time.sleep(10)
                if is_market_open():
                    break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("\n  Bot stopped by user. Goodbye.")
