#!/usr/bin/env python3
"""
Bot Runner — Auto-schedules scanner + paper trader during NSE market hours.

Market hours    : 9:15 AM – 3:30 PM IST, Monday–Friday
Scan interval   : every 5 minutes during market hours
EOD baseline    : 3:15 PM — scanner + baseline entries
EOD confirm     : 3:27 PM — intraday filter + shadow confirm entries

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
EOD_SCAN_H,     EOD_SCAN_M     = 15, 15   # 3:15 PM — scanner + baseline entries
EOD_CONFIRM_H,  EOD_CONFIRM_M  = 15, 27   # 3:27 PM — intraday confirm + shadow book
SCAN_INTERVAL_SEC = 300   # 5 minutes


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _minutes_now():
    now = datetime.now()
    return now.hour * 60 + now.minute


def is_market_open():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = _minutes_now()
    open_t  = MARKET_OPEN_H  * 60 + MARKET_OPEN_M
    close_t = MARKET_CLOSE_H * 60 + MARKET_CLOSE_M
    return open_t <= t <= close_t


def is_eod_scan_time():
    """True from 3:15 PM until 3:27 PM (baseline scan window)."""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = _minutes_now()
    scan_t = EOD_SCAN_H * 60 + EOD_SCAN_M
    confirm_t = EOD_CONFIRM_H * 60 + EOD_CONFIRM_M
    return scan_t <= t < confirm_t


def is_eod_confirm_time():
    """True from 3:27 PM until market close."""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = _minutes_now()
    return t >= EOD_CONFIRM_H * 60 + EOD_CONFIRM_M


def seconds_until_open():
    now = datetime.now()
    nxt = now.replace(hour=MARKET_OPEN_H, minute=MARKET_OPEN_M, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return max(0, int((nxt - now).total_seconds()))


def run_intraday_check():
    """Every 5-min intraday: SL/target on baseline + confirm portfolios."""
    import importlib
    import paper_trader
    importlib.reload(paper_trader)

    log("── SL/Target Check (baseline + confirm) ─────")
    paper_trader.run(intraday_only=True)
    log("── Cycle complete ───────────────────────────")


def run_eod_scan():
    """3:15 PM: scanner + baseline entries + save shortlist."""
    import importlib
    import long_buildup_scanner
    import paper_trader
    import eod_confirm
    importlib.reload(long_buildup_scanner)
    importlib.reload(paper_trader)
    importlib.reload(eod_confirm)

    log("── EOD Scan (3:15) — Scanner + Baseline ─────")
    long_buildup_scanner.scan()
    paper_trader.run()
    eod_confirm.save_shortlist()
    log("── EOD scan phase complete ──────────────────")


def run_eod_confirm(notify=False):
    """3:27 PM: intraday confirm filters + shadow entries + Excel + WhatsApp."""
    import importlib
    import eod_confirm
    import run_once
    importlib.reload(eod_confirm)
    importlib.reload(run_once)

    log("── EOD Confirm (3:27) — Intraday filters ───")
    eod_confirm.run_confirm()
    if notify:
        log("── Sending WhatsApp summary ─────────────────")
        run_once.send_whatsapp(run_once.build_summary())
    log("── EOD confirm phase complete ─────────────────")


def main():
    once = "--once" in sys.argv

    log("=" * 60)
    log("  AUTO TRADER BOT — Starting up")
    log(f"  Market hours : {MARKET_OPEN_H:02d}:{MARKET_OPEN_M:02d} – "
        f"{MARKET_CLOSE_H:02d}:{MARKET_CLOSE_M:02d} IST  Mon–Fri")
    log(f"  EOD baseline : {EOD_SCAN_H:02d}:{EOD_SCAN_M:02d} IST (scanner + baseline entries)")
    log(f"  EOD confirm  : {EOD_CONFIRM_H:02d}:{EOD_CONFIRM_M:02d} IST (intraday filter + shadow book)")
    log(f"  Scan interval: every {SCAN_INTERVAL_SEC // 60} minutes (SL/target check)")
    log("  Press Ctrl+C to stop")
    log("=" * 60)

    eod_scan_done_date = None
    eod_confirm_done_date = None

    while True:
        today = datetime.now().strftime("%Y-%m-%d")

        if is_market_open():
            if is_eod_confirm_time() and eod_confirm_done_date != today:
                if eod_scan_done_date != today:
                    log("  Confirm due but scan not run — running 3:15 scan first...")
                    try:
                        run_eod_scan()
                        eod_scan_done_date = today
                    except KeyboardInterrupt:
                        raise
                    except Exception:
                        log("  !! EOD scan failed:")
                        traceback.print_exc()
                try:
                    run_eod_confirm(notify=True)
                    eod_confirm_done_date = today
                    log("  EOD confirm complete ✓")
                except KeyboardInterrupt:
                    raise
                except Exception:
                    log("  !! EOD confirm failed:")
                    traceback.print_exc()
            elif is_eod_scan_time() and eod_scan_done_date != today:
                log(f"  EOD scan triggered ({EOD_SCAN_H:02d}:{EOD_SCAN_M:02d})...")
                try:
                    run_eod_scan()
                    eod_scan_done_date = today
                    log("  EOD scan complete ✓")
                except KeyboardInterrupt:
                    raise
                except Exception:
                    log("  !! EOD scan failed:")
                    traceback.print_exc()
            else:
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
            now  = datetime.now()
            secs = seconds_until_open()
            h, m = divmod(secs // 60, 60)

            if now.weekday() >= 5:
                reason = f"Weekend ({['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][now.weekday()]})"
            elif _minutes_now() < MARKET_OPEN_H * 60 + MARKET_OPEN_M:
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
