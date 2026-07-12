#!/usr/bin/env python3
"""
Bot Runner — Auto-schedules scanner + paper trader during NSE market hours.

Market hours    : 9:15 AM – 3:30 PM IST, Monday–Friday
Scan interval   : every 5 minutes during market hours
EOD baseline    : 3:25 PM — scanner + baseline entries (wall-clock wake)
EOD confirm     : 3:30 PM — intraday filter + shadow confirm entries

Usage:
  python3 run_bot.py          # runs forever, Ctrl+C to stop
  python3 run_bot.py --once   # single cycle (for cron / testing)
"""

import os
import sys
import time
import traceback
from datetime import datetime, timedelta

import bot_status

MARKET_OPEN_H,  MARKET_OPEN_M  =  9, 15
MARKET_CLOSE_H, MARKET_CLOSE_M = 15, 30
EOD_SCAN_H,     EOD_SCAN_M     = 15, 25   # 3:25 PM — scanner + baseline entries
EOD_CONFIRM_H,  EOD_CONFIRM_M  = 15, 30   # 3:30 PM — intraday confirm + shadow book
SCAN_INTERVAL_SEC = 300        # 5 minutes (intraday SL/target)
PRE_EOD_LOOKAHEAD_SEC = 300    # skip intraday checks within 5 min of EOD scan


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


def _today_at(h, m):
    now = datetime.now()
    return now.replace(hour=h, minute=m, second=0, microsecond=0)


def is_eod_scan_time():
    """True from 3:25:00 PM until market close (baseline scan window)."""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    scan_at = _today_at(EOD_SCAN_H, EOD_SCAN_M)
    close_at = _today_at(MARKET_CLOSE_H, MARKET_CLOSE_M)
    return scan_at <= now <= close_at


def is_eod_confirm_time():
    """True from 3:30:00 PM until market close."""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    return now >= _today_at(EOD_CONFIRM_H, EOD_CONFIRM_M)


def seconds_until_eod_scan():
    """Seconds until today's 3:25:00 PM (0 if already past)."""
    now = datetime.now()
    target = _today_at(EOD_SCAN_H, EOD_SCAN_M)
    return max(0, int((target - now).total_seconds()))


def approaching_eod_scan(eod_scan_done_date):
    """True when within PRE_EOD_LOOKAHEAD_SEC of 3:25 and scan not yet run today."""
    today = datetime.now().strftime("%Y-%m-%d")
    if eod_scan_done_date == today:
        return False
    if datetime.now().weekday() >= 5:
        return False
    secs = seconds_until_eod_scan()
    return 0 < secs <= PRE_EOD_LOOKAHEAD_SEC


def next_5min_boundary():
    """Seconds until the next clock-aligned 5-min tick (e.g. :15, :20, :25)."""
    now = datetime.now()
    minute = (now.minute // 5 + 1) * 5
    if minute >= 60:
        nxt = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    else:
        nxt = now.replace(minute=minute, second=0, microsecond=0)
    return max(1, int((nxt - now).total_seconds()))


def compute_sleep_seconds(eod_scan_done_date):
    """Sleep until EOD scan time when close; otherwise align to 5-min clock."""
    today = datetime.now().strftime("%Y-%m-%d")
    secs_to_scan = seconds_until_eod_scan()
    if eod_scan_done_date != today and 0 < secs_to_scan <= SCAN_INTERVAL_SEC:
        return secs_to_scan
    return min(next_5min_boundary(), SCAN_INTERVAL_SEC)


def seconds_until_open():
    now = datetime.now()
    nxt = now.replace(hour=MARKET_OPEN_H, minute=MARKET_OPEN_M, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return max(0, int((nxt - now).total_seconds()))


def wait_until_confirm():
    """Block until 3:30 PM so confirm runs after scan finishes."""
    now = datetime.now()
    target = _today_at(EOD_CONFIRM_H, EOD_CONFIRM_M)
    if now >= target:
        return
    secs = int((target - now).total_seconds())
    log(f"  Waiting until {EOD_CONFIRM_H:02d}:{EOD_CONFIRM_M:02d} for confirm ({secs // 60}m {secs % 60}s)...")
    time.sleep(secs)


def _shortlist_pending_confirm():
    """True if today's shortlist exists but confirm log was never written."""
    import json
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eod_shortlist.json")
    if not os.path.exists(path):
        return False
    today = datetime.now().strftime("%Y-%m-%d")
    with open(path) as f:
        data = json.load(f)
    if data.get("date") != today:
        return False
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eod_confirm_log.json")
    if not os.path.exists(log_path):
        return True
    with open(log_path) as f:
        log_data = json.load(f)
    return not any(e.get("date") == today for e in log_data.get("entries", []))


def run_intraday_check():
    """Every 5-min intraday: SL/target on baseline + confirm portfolios."""
    import importlib
    import paper_trader
    importlib.reload(paper_trader)

    bot_status.write_status(
        phase="intraday_check",
        market_open=True,
        last_error=None,
        next_wake_at=(datetime.now() + timedelta(seconds=SCAN_INTERVAL_SEC)).strftime("%H:%M:%S"),
    )
    bot_status.log_activity("cycle", "SL/target check started")
    log("── SL/Target Check (baseline + confirm) ─────")
    try:
        paper_trader.run(intraday_only=True)
        bot_status.write_status(phase="idle", angel_ok=True)
        bot_status.log_activity("cycle", "SL/target check complete")
    except Exception as e:
        bot_status.write_status(phase="error", last_error=str(e), last_error_at=datetime.now().isoformat())
        bot_status.log_activity("error", f"Intraday check failed: {e}")
        raise
    log("── Cycle complete ───────────────────────────")


def run_eod_scan():
    """3:25 PM: scanner + baseline entries + save shortlist."""
    import importlib
    import long_buildup_scanner
    import paper_trader
    import eod_confirm
    importlib.reload(long_buildup_scanner)
    importlib.reload(paper_trader)
    importlib.reload(eod_confirm)

    bot_status.write_status(phase="eod_scan", market_open=True)
    bot_status.log_activity("eod", "EOD scan (3:25) started")
    log("── EOD Scan (3:25) — Scanner + Baseline ─────")
    long_buildup_scanner.scan()
    paper_trader.run()
    eod_confirm.save_shortlist()
    today = datetime.now().strftime("%Y-%m-%d")
    bot_status.write_status(eod_scan_done_today=today, phase="eod_scan_done")
    bot_status.log_activity("eod", "EOD scan complete — shortlist saved")
    log("── EOD scan phase complete ──────────────────")


def run_eod_confirm(notify=False):
    """3:30 PM: intraday confirm filters + shadow entries + Excel + WhatsApp."""
    import importlib
    import eod_confirm
    import run_once
    importlib.reload(eod_confirm)
    importlib.reload(run_once)

    bot_status.write_status(phase="eod_confirm", market_open=True)
    bot_status.log_activity("eod", "EOD confirm (3:30) started")
    log("── EOD Confirm (3:30) — Intraday filters ───")
    eod_confirm.run_confirm()
    if notify:
        log("── Sending WhatsApp summary ─────────────────")
        run_once.send_whatsapp(run_once.build_summary())
    today = datetime.now().strftime("%Y-%m-%d")
    bot_status.write_status(eod_confirm_done_today=today, phase="idle")
    bot_status.log_activity("eod", "EOD confirm complete")
    log("── EOD confirm phase complete ─────────────────")


def run_eod_full_cycle(notify=False):
    """3:25 scan → wait until 3:30 → confirm."""
    run_eod_scan()
    wait_until_confirm()
    run_eod_confirm(notify=notify)


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

    bot_status.write_status(
        phase="starting",
        market_open=is_market_open(),
        started_at=datetime.now().isoformat(),
    )
    bot_status.log_activity("system", "Bot started")

    eod_scan_done_date = None
    eod_confirm_done_date = None

    while True:
        today = datetime.now().strftime("%Y-%m-%d")

        if is_market_open():
            if is_eod_scan_time() and eod_scan_done_date != today:
                log(f"  EOD full cycle triggered ({EOD_SCAN_H:02d}:{EOD_SCAN_M:02d} → {EOD_CONFIRM_H:02d}:{EOD_CONFIRM_M:02d})...")
                try:
                    run_eod_full_cycle(notify=True)
                    eod_scan_done_date = today
                    eod_confirm_done_date = today
                    log("  EOD full cycle complete ✓")
                except KeyboardInterrupt:
                    raise
                except Exception:
                    log("  !! EOD full cycle failed:")
                    traceback.print_exc()
            elif is_eod_confirm_time() and eod_confirm_done_date != today:
                if eod_scan_done_date != today:
                    log("  Confirm due but scan not run — running scan first...")
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
            elif approaching_eod_scan(eod_scan_done_date):
                secs = seconds_until_eod_scan()
                wake_at = _today_at(EOD_SCAN_H, EOD_SCAN_M).strftime("%H:%M:%S")
                log(f"  Pre-EOD — skipping intraday check, sleeping until "
                    f"{EOD_SCAN_H:02d}:{EOD_SCAN_M:02d} ({secs}s)...")
                bot_status.write_status(
                    phase="pre_eod",
                    market_open=True,
                    next_wake_at=wake_at,
                )
                time.sleep(secs)
                continue

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

            sleep_secs = compute_sleep_seconds(eod_scan_done_date)
            wake_at = (datetime.now() + timedelta(seconds=sleep_secs)).strftime("%H:%M:%S")
            log(f"  Next check in {sleep_secs // 60}m {sleep_secs % 60}s (~{wake_at})")
            bot_status.write_status(next_wake_at=wake_at)
            time.sleep(sleep_secs)

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
            bot_status.write_status(
                phase="sleeping",
                market_open=False,
                sleep_reason=reason,
                next_wake_at=(datetime.now() + timedelta(minutes=10)).strftime("%H:%M:%S"),
            )

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
