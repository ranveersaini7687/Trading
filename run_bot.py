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
POST_MARKET_H,  POST_MARKET_M  = 16, 40   # bhav copy usually available by 4:30 PM
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


def is_post_market_window():
    """True from 4:40 PM onwards on weekdays (until midnight)."""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t      = now.hour * 60 + now.minute
    post_t = POST_MARKET_H * 60 + POST_MARKET_M
    return t >= post_t


def bhav_copy_fetched_today():
    """True if today's bhav copy cache file exists (scan succeeded)."""
    trade_date = datetime.now().strftime("%Y%m%d")
    return os.path.exists(os.path.join("cache", f"bhavcopy_{trade_date}.json"))


def seconds_until_open():
    now = datetime.now()
    nxt = now.replace(hour=MARKET_OPEN_H, minute=MARKET_OPEN_M, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return max(0, int((nxt - now).total_seconds()))


def run_cycle(notify=False):
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
    post_market_done_date = None   # track which date post-market scan completed

    log("=" * 60)
    log("  AUTO TRADER BOT — Starting up")
    log(f"  Market hours : {MARKET_OPEN_H:02d}:{MARKET_OPEN_M:02d} – "
        f"{MARKET_CLOSE_H:02d}:{MARKET_CLOSE_M:02d} IST  Mon–Fri")
    log(f"  Post-market  : {POST_MARKET_H:02d}:{POST_MARKET_M:02d} IST (bhav copy scan, retries every 5 min)")
    log(f"  Scan interval: every {SCAN_INTERVAL_SEC // 60} minutes")
    log("  Press Ctrl+C to stop")
    log("=" * 60)

    while True:
        today = datetime.now().strftime("%Y-%m-%d")

        if is_market_open():
            # ── Intraday scan every 5 min ──────────────────────────
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

        elif is_post_market_window() and post_market_done_date != today:
            # ── Post-market bhav copy scan ─────────────────────────
            if bhav_copy_fetched_today():
                log("  Post-market: bhav copy already cached — skipping extra scan.")
                post_market_done_date = today
                if once:
                    break
                for _ in range(60):
                    time.sleep(10)
                    if is_market_open():
                        break
            else:
                log(f"  Post-market scan — fetching bhav copy (retry every 5 min)...")
                try:
                    run_cycle(notify=True)   # daily WhatsApp fires here once
                    post_market_done_date = today
                    log("  Post-market scan complete ✓")
                except KeyboardInterrupt:
                    raise
                except Exception:
                    log("  !! Post-market cycle failed — retrying in 5 min:")
                    traceback.print_exc()

                if once:
                    break
                time.sleep(SCAN_INTERVAL_SEC)

        else:
            # ── Sleep until next event ─────────────────────────────
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
                if is_market_open() or (is_post_market_window() and post_market_done_date != today):
                    break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("\n  Bot stopped by user. Goodbye.")
