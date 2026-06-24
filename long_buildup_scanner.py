#!/usr/bin/env python3
"""
Auto Trader - Long Buildup Scanner
Fetches fresh data on every run — no caching.
"""

import os
import requests
import time
import json
import functools
from datetime import datetime
from angel_api import AngelOneAPI

angel = AngelOneAPI()

# ── Config ────────────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
}

MIN_PCR           = 0.8
MIN_OI_CHANGE_PCT = 2.0
MIN_VOLUME_RATIO  = 1.5   # today's volume must be >= 1.5× 20-day average
EMA_PERIODS       = (9, 21, 50)   # bullish stack: price > 9 EMA > 21 EMA > 50 EMA
SKIP_SYMBOLS      = {"NIFTY", "FINNIFTY", "BANKNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX", "NIFTYNXT50"}
CACHE_DIR         = "cache"
MAX_RETRIES       = 3
RETRY_DELAY_SEC   = 3

# FII macro sentiment thresholds (₹ Crore, rolling 5-day net)
FII_LOOKBACK_DAYS  = 5
FII_BEARISH_THRESH = -3000   # sold > 3000 Cr → BEARISH (block longs)
FII_CAUTIOUS_THRESH = -1500  # sold 1500-3000 Cr → CAUTIOUS (warn)
FII_BULLISH_THRESH  =  1500  # bought > 1500 Cr → BULLISH


# ── Logging ───────────────────────────────────────────────────────────────────
def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ── Retry decorator ───────────────────────────────────────────────────────────
def retry(func):
    """Retry a function up to MAX_RETRIES times with exponential backoff."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        last_err = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_err = e
                wait = RETRY_DELAY_SEC * attempt
                if attempt < MAX_RETRIES:
                    log(f"  !! [{func.__name__}] attempt {attempt}/{MAX_RETRIES} failed: {e} — retrying in {wait}s")
                    time.sleep(wait)
                else:
                    log(f"  !! [{func.__name__}] all {MAX_RETRIES} attempts failed: {e}")
        raise last_err
    return wrapper


# ── NSE session ───────────────────────────────────────────────────────────────
def init_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    session.get("https://www.nseindia.com", timeout=15)
    time.sleep(2)
    session.get("https://www.nseindia.com/market-data/live-equity-market", timeout=15)
    time.sleep(1)
    return session


# ── Step 1: OI spurt stocks ───────────────────────────────────────────────────
@retry
def _fetch_oi_spurts(session):
    url = "https://www.nseindia.com/api/live-analysis-oi-spurts-underlyings"
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    stocks = []
    for s in data.get("data", []):
        sym = s.get("symbol", "")
        if sym in SKIP_SYMBOLS or not sym:
            continue
        prev_oi, latest_oi = s.get("prevOI", 0), s.get("latestOI", 0)
        if prev_oi == 0:
            continue
        stocks.append({
            "symbol":     sym,
            "latest_oi":  latest_oi,
            "prev_oi":    prev_oi,
            "oi_chg":     round(((latest_oi - prev_oi) / prev_oi) * 100, 2),
            "spot_price": s.get("underlyingValue", 0),
            "nse_ts":     data.get("timestamp", ""),
        })
    return stocks


def get_oi_spurt_stocks(session):
    log("Step 1 — OI spurt stocks (NSE)")
    stocks = _fetch_oi_spurts(session)
    log(f"  → {len(stocks)} F&O stocks with OI UP | {stocks[0]['nse_ts'] if stocks else ''}")
    return stocks


# ── Step 2: Live quotes (Angel One) ──────────────────────────────────────────
def get_price_changes(symbols):
    """
    Returns ({symbol: price_chg_pct}, {symbol: ltp}, {symbol: volume}).
    Uses Angel One SmartAPI for live LTP + previous close + today's volume.
    """
    log(f"Step 2 — Live quotes for {len(symbols)} stocks (Angel One)")
    try:
        quotes = angel.get_quotes(symbols)
    except Exception as e:
        log(f"  !! Angel One quote fetch failed: {e}")
        return {s: None for s in symbols}, {s: None for s in symbols}, {s: 0 for s in symbols}

    changes, ltps, volumes = {}, {}, {}
    for sym in symbols:
        q = quotes.get(sym)
        if q and q["ltp"] and q["prev_close"]:
            changes[sym] = round((q["ltp"] - q["prev_close"]) / q["prev_close"] * 100, 2)
            ltps[sym]    = q["ltp"]
            volumes[sym] = q.get("volume", 0)
        else:
            changes[sym] = None
            ltps[sym]    = None
            volumes[sym] = 0

    valid = sum(v is not None for v in changes.values())
    log(f"  → Live quotes fetched for {valid}/{len(symbols)} stocks")
    return changes, ltps, volumes


# ── Step 3b: 20-day average volume ───────────────────────────────────────────
def get_avg_volumes_data(symbols):
    log(f"Step 3b — 20-day avg volumes for {len(symbols)} stocks (Angel One)")
    if not symbols:
        return {}
    avg_vols = angel.get_avg_volumes(symbols)
    log(f"  → Avg volumes fetched for {len(avg_vols)} stocks")
    return avg_vols


# ── Step 3c: EMA stack ────────────────────────────────────────────────────────
def get_ema_stack_data(symbols):
    log(f"Step 3c — EMA stack ({'/'.join(str(p) for p in EMA_PERIODS)}) for {len(symbols)} stocks")
    if not symbols:
        return {}
    ema_data = angel.get_ema_stack(symbols)
    log(f"  → EMA stack computed for {len(ema_data)} stocks")
    return ema_data


# ── Step 4: Live option chain — PCR + liquidity (Angel One NFO) ──────────────
def get_live_pcr_data(symbols):
    """
    Fetch live PCR for each symbol via Angel One NFO option chain (all strikes, near expiry).
    Returns ({symbol: pcr}, {symbol: is_liquid}).
    """
    log(f"Step 4 — Live option chain PCR for {len(symbols)} stocks (Angel One NFO)")
    if not symbols:
        return {}, set()
    pcr_map, liquid_stocks = {}, set()
    for sym in symbols:
        try:
            pcr, liquid = angel.get_pcr(sym)
            if pcr is not None:
                pcr_map[sym] = pcr
            if liquid:
                liquid_stocks.add(sym)
        except Exception as e:
            log(f"  !! {sym}: Angel One PCR failed — {e}")

    log(f"  → PCR fetched for {len(pcr_map)}/{len(symbols)} stocks  |  {len(liquid_stocks)} liquid")
    return pcr_map, liquid_stocks


# ── Step 7: FII/DII flow data ─────────────────────────────────────────────────
@retry
def _fetch_fii_dii(session):
    url = "https://www.nseindia.com/api/fiidiiTradeReact"
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    rows = resp.json()
    result = {"date": rows[0].get("date", "") if rows else ""}
    for row in rows:
        cat = row.get("category", "").upper()
        def _num(field):
            return float(str(row.get(field, "0")).replace(",", "") or "0")
        if "FII" in cat:
            result.update({"fii_net": _num("netValue"), "fii_buy": _num("buyValue"), "fii_sell": _num("sellValue")})
        elif "DII" in cat:
            result.update({"dii_net": _num("netValue"), "dii_buy": _num("buyValue"), "dii_sell": _num("sellValue")})
    return result


def get_fii_dii_data(session, trade_date):
    log("Step 7 — FII/DII institutional flow (NSE)")
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(CACHE_DIR, f"fii_dii_{trade_date}.json")
    if os.path.exists(cache_file):
        with open(cache_file) as f:
            data = json.load(f)
        log(f"  → FII net: ₹{data.get('fii_net', 0):+,.2f} Cr  DII net: ₹{data.get('dii_net', 0):+,.2f} Cr (cached)")
        return data
    data = _fetch_fii_dii(session)
    with open(cache_file, "w") as f:
        json.dump(data, f)
    log(f"  → FII net: ₹{data.get('fii_net', 0):+,.2f} Cr  DII net: ₹{data.get('dii_net', 0):+,.2f} Cr")
    return data


def compute_fii_sentiment(lookback_days=FII_LOOKBACK_DAYS):
    """Read the last N fii_dii_*.json cache files and compute rolling FII net sentiment."""
    if not os.path.isdir(CACHE_DIR):
        return {"sentiment": "UNKNOWN", "fii_nd_net": 0.0, "days": 0, "records": []}

    files = sorted(
        [f for f in os.listdir(CACHE_DIR) if f.startswith("fii_dii_") and f.endswith(".json")],
        reverse=True,
    )[:lookback_days]

    if not files:
        return {"sentiment": "UNKNOWN", "fii_nd_net": 0.0, "days": 0, "records": []}

    total, records = 0.0, []
    for fname in files:
        with open(os.path.join(CACHE_DIR, fname)) as f:
            data = json.load(f)
        fii_net = float(data.get("fii_net", 0) or 0)
        dii_net = float(data.get("dii_net", 0) or 0)
        total  += fii_net
        records.append({"date": data.get("date", fname[8:16]), "fii_net": fii_net, "dii_net": dii_net})

    n = len(records)
    label = (
        "BEARISH"  if total <= FII_BEARISH_THRESH  else
        "CAUTIOUS" if total <= FII_CAUTIOUS_THRESH else
        "BULLISH"  if total >= FII_BULLISH_THRESH  else
        "NEUTRAL"
    )
    return {"sentiment": label, "fii_nd_net": round(total, 2), "days": n, "records": records}


# ── Main scan ─────────────────────────────────────────────────────────────────
def scan():
    trade_date = datetime.now().strftime("%Y%m%d")

    log("=" * 65)
    log("  AUTO TRADER — Long Buildup Scanner")
    log(f"  Date  : {datetime.now().strftime('%d %b %Y %H:%M IST')}")
    log(f"  Filter: Price UP + OI >= +{MIN_OI_CHANGE_PCT}% + Vol >= {MIN_VOLUME_RATIO}x + EMA {'/'.join(str(p) for p in EMA_PERIODS)} stack + PCR >= {MIN_PCR}")
    log(f"  Macro : FII {FII_LOOKBACK_DAYS}-day rolling  |  BEARISH<{FII_BEARISH_THRESH}Cr, CAUTIOUS<{FII_CAUTIOUS_THRESH}Cr")
    log("=" * 65)

    session = init_session()

    # Step 7 — FII/DII (cached per day; used for rolling 5-day sentiment)
    try:
        get_fii_dii_data(session, trade_date)
    except Exception as e:
        log(f"  !! FII/DII fetch failed: {e} — macro will use cached history only")

    macro = compute_fii_sentiment(FII_LOOKBACK_DAYS)
    log(f"\n  ── FII Macro ({macro['days']}-day rolling) ──")
    for rec in macro["records"]:
        bar  = "▼" if rec["fii_net"] < 0 else "▲"
        sign = "+" if rec["fii_net"] >= 0 else ""
        log(f"     {rec['date']}  FII: {sign}{rec['fii_net']:>9,.2f} Cr  DII: {'+' if rec['dii_net'] >= 0 else ''}{rec['dii_net']:>9,.2f} Cr  {bar}")
    log(f"  ── {macro['days']}D FII Net: ₹{macro['fii_nd_net']:+,.2f} Cr  →  Sentiment: {macro['sentiment']} ──\n")

    if macro["sentiment"] == "BEARISH":
        log("  *** MACRO ALERT: FII aggressively SELLING — HIGH risk of false long signals ***")
    elif macro["sentiment"] == "CAUTIOUS":
        log("  **  MACRO CAUTION: FII net selling — verify each trade carefully  **")

    # Step 1
    oi_stocks = get_oi_spurt_stocks(session)
    symbols   = [s["symbol"] for s in oi_stocks]
    oi_map    = {s["symbol"]: s for s in oi_stocks}

    # Step 2 — Angel One live quotes
    price_changes, ltps, volumes = get_price_changes(symbols)

    # Update spot_price with live LTP for accurate entry price
    for sym in symbols:
        if ltps.get(sym):
            oi_map[sym]["spot_price"] = ltps[sym]

    # Step 3 — Long buildup filter
    log(f"Step 3 — Long Buildup filter (Price UP + OI >= +{MIN_OI_CHANGE_PCT}%)")
    long_buildup = []
    for sym in symbols:
        price_chg = price_changes.get(sym)
        oi_chg    = oi_map[sym]["oi_chg"]
        if price_chg is None:
            log(f"  {sym:<16} — no price data")
            continue
        is_long = price_chg > 0 and oi_chg >= MIN_OI_CHANGE_PCT
        marker  = "✓" if is_long else " "
        log(f"  {marker} {sym:<15} Price: {price_chg:+.2f}%  OI: {oi_chg:+.2f}%")
        if is_long:
            long_buildup.append({**oi_map[sym], "price_chg": price_chg})
    log(f"  → {len(long_buildup)} long buildup stocks")

    # Step 3b — Volume filter
    lb_syms_vol  = [s["symbol"] for s in long_buildup]
    avg_vols     = get_avg_volumes_data(lb_syms_vol)
    log(f"Step 3b — Volume filter (today >= {MIN_VOLUME_RATIO}x 20D avg)")
    vol_passed = []
    for stock in long_buildup:
        sym       = stock["symbol"]
        today_vol = volumes.get(sym, 0)
        avg_vol   = avg_vols.get(sym, 0)
        ratio     = round(today_vol / avg_vol, 2) if avg_vol > 0 else 0
        passes    = ratio >= MIN_VOLUME_RATIO
        marker    = "✓" if passes else " "
        log(f"  {marker} {sym:<15} Vol: {today_vol:>10,}  Avg20D: {avg_vol:>10,}  {ratio:.2f}x")
        if passes:
            vol_passed.append({**stock, "vol_ratio": ratio})
    log(f"  → {len(vol_passed)} stocks passed volume filter")

    # Step 3c — EMA stack filter
    ema_syms  = [s["symbol"] for s in vol_passed]
    ema_data  = get_ema_stack_data(ema_syms)
    log(f"Step 3c — EMA stack filter (Price > 9EMA > 21EMA > 50EMA)")
    ema_passed = []
    for stock in vol_passed:
        sym   = stock["symbol"]
        ed    = ema_data.get(sym, {})
        e9, e21, e50 = ed.get("ema9"), ed.get("ema21"), ed.get("ema50")
        ok    = ed.get("passes", False)
        marker = "✓" if ok else " "
        e9s   = f"{e9:.1f}"  if e9  else "N/A"
        e21s  = f"{e21:.1f}" if e21 else "N/A"
        e50s  = f"{e50:.1f}" if e50 else "N/A"
        log(f"  {marker} {sym:<15} 9EMA: {e9s:>8}  21EMA: {e21s:>8}  50EMA: {e50s:>8}  {'PASS' if ok else 'FAIL'}")
        if ok:
            ema_passed.append({**stock, "ema9": e9, "ema21": e21, "ema50": e50})
    log(f"  → {len(ema_passed)} stocks passed EMA stack filter")

    # Step 4 — Live option chain PCR (only for EMA-confirmed stocks)
    lb_symbols  = [s["symbol"] for s in ema_passed]
    try:
        pcr_map, liquid_stocks = get_live_pcr_data(lb_symbols)
    except Exception as e:
        log(f"  !! Option chain fetch failed: {e}")
        pcr_map, liquid_stocks = {}, set()

    # Step 5 — PCR + liquidity filter
    log(f"Step 5 — PCR >= {MIN_PCR} + Liquid only")
    results = []
    for stock in ema_passed:
        sym     = stock["symbol"]
        pcr     = pcr_map.get(sym)
        liquid  = sym in liquid_stocks
        pcr_str = f"{pcr:.2f}" if pcr is not None else "N/A"
        if pcr is not None and pcr >= MIN_PCR and liquid:
            results.append({**stock, "pcr": pcr})
            log(f"  ✓✓ {sym:<15} PCR: {pcr_str}  liquid: yes  → MATCH")
        else:
            reasons = []
            if pcr is None or pcr < MIN_PCR: reasons.append(f"PCR {pcr_str}")
            if not liquid: reasons.append("illiquid")
            log(f"     {sym:<15} PCR: {pcr_str}  liquid: {'yes' if liquid else 'no '}  → {', '.join(reasons)}")

    # ── Results table ──────────────────────────────────────────────────────────
    MACRO_ICON = {"BULLISH": "▲", "NEUTRAL": "─", "CAUTIOUS": "!", "BEARISH": "▼", "UNKNOWN": "?"}
    sentiment  = macro["sentiment"]
    icon       = MACRO_ICON.get(sentiment, "?")

    log("\n" + "=" * 92)
    log(f"  RESULTS — Long Buildup + PCR >= {MIN_PCR}  |  {datetime.now().strftime('%d %b %Y')}  |  FII Macro: [{icon}] {sentiment}")
    log("=" * 92)

    if macro["sentiment"] in ("BEARISH", "CAUTIOUS"):
        log(f"  ⚠  FII sold ₹{abs(macro['fii_nd_net']):,.0f} Cr over {macro['days']} days — treat these as HIGH RISK longs")

    if results:
        results_sorted = sorted(results, key=lambda x: x["oi_chg"], reverse=True)
        print(f"\n{'#':<4} {'Symbol':<15} {'Spot Price':>10} {'Price Chg%':>11} {'OI Chg%':>10} {'Vol':>6} {'PCR':>7}  {'Macro':>9}")
        print("-" * 80)
        for idx, r in enumerate(results_sorted, 1):
            macro_col = f"[{icon}] {sentiment}"
            print(f"{idx:<4} {r['symbol']:<15} {r['spot_price']:>10.2f} {r['price_chg']:>+10.2f}% {r['oi_chg']:>+9.2f}% {r.get('vol_ratio', 0):>5.1f}x {r['pcr']:>7.2f}  {macro_col}")
        print()
        log(f"Total matched: {len(results)} stocks  |  FII {macro['days']}D Net: ₹{macro['fii_nd_net']:+,.2f} Cr  [{icon}] {sentiment}")
    else:
        log("No stocks matched the criteria.")

    output = {
        "scan_time": datetime.now().isoformat(),
        "criteria": {"min_oi_chg_pct": MIN_OI_CHANGE_PCT, "min_pcr": MIN_PCR},
        "macro":    {"sentiment": sentiment, "fii_5d_net_cr": macro["fii_nd_net"], "days": macro["days"], "history": macro["records"]},
        "summary":  {"oi_spurt_stocks": len(oi_stocks), "long_buildup": len(long_buildup), "vol_passed": len(vol_passed), "ema_passed": len(ema_passed), "matched": len(results)},
        "results":  results,
    }
    with open("scan_results.json", "w") as f:
        json.dump(output, f, indent=2)
    log("Results saved → scan_results.json")
    log("=" * 82)


if __name__ == "__main__":
    scan()
