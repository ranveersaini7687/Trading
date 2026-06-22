#!/usr/bin/env python3
"""
Auto Trader - Long Buildup Scanner
Each step fetches data, caches it to disk, and retries up to 3 times on failure.
On subsequent iterations the cached copy is returned instantly without re-fetching.

Cache lives in ./cache/ — keyed by step + trade date.
TTL: OI / prices = 5 min (live); Bhav copy / market cap = full day (date-keyed).
"""

import requests
import time
import json
import io
import os
import zipfile
import functools
import yfinance as yf
import pandas as pd
from datetime import datetime

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
SKIP_SYMBOLS      = {"NIFTY", "FINNIFTY", "BANKNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"}
CACHE_DIR         = "cache"
LIVE_TTL_SEC      = 300   # 5 min for intraday data (OI spurts, prices)
MAX_RETRIES       = 3
RETRY_DELAY_SEC   = 3

# FII macro sentiment thresholds (₹ Crore, rolling 5-day net)
FII_LOOKBACK_DAYS  = 5
FII_BEARISH_THRESH = -3000   # sold > 3000 Cr → BEARISH  (block longs)
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


# ── Cache helpers ─────────────────────────────────────────────────────────────
os.makedirs(CACHE_DIR, exist_ok=True)


def _cache_path(key):
    return os.path.join(CACHE_DIR, f"{key}.json")


def cache_load(key, ttl_sec=None):
    """
    Return cached value for key, or None if missing / expired.
    ttl_sec=None means date-keyed (valid all day — key already contains the date).
    """
    path = _cache_path(key)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        entry = json.load(f)
    if ttl_sec is not None:
        age = time.time() - entry.get("ts", 0)
        if age > ttl_sec:
            log(f"  [cache] {key}: expired ({age:.0f}s > {ttl_sec}s TTL)")
            return None
    log(f"  [cache] {key}: HIT")
    return entry["data"]


def cache_save(key, data):
    path = _cache_path(key)
    with open(path, "w") as f:
        json.dump({"ts": time.time(), "data": data}, f)
    log(f"  [cache] {key}: saved")


# ── Cache cleanup ─────────────────────────────────────────────────────────────
def cleanup_old_cache(days=30):
    """Delete cache files not modified in the last `days` days."""
    cutoff = time.time() - days * 86400
    removed, total = 0, 0
    for fname in os.listdir(CACHE_DIR):
        if not fname.endswith(".json"):
            continue
        total += 1
        fpath = os.path.join(CACHE_DIR, fname)
        if os.stat(fpath).st_mtime < cutoff:
            os.remove(fpath)
            removed += 1
            log(f"  [cache] deleted old file: {fname}")
    if removed:
        log(f"  [cache cleanup] removed {removed}/{total} files older than {days} days")
    else:
        log(f"  [cache cleanup] {total} files checked, none older than {days} days")


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


def get_oi_spurt_stocks(session, trade_date):
    log("Step 1 — OI spurt stocks (NSE)")
    key = f"oi_spurts_{trade_date}"
    cached = cache_load(key, ttl_sec=LIVE_TTL_SEC)
    if cached is not None:
        log(f"  → {len(cached)} stocks (from cache)")
        return cached
    stocks = _fetch_oi_spurts(session)
    cache_save(key, stocks)
    log(f"  → {len(stocks)} F&O stocks with OI UP | {stocks[0]['nse_ts'] if stocks else ''}")
    return stocks


# ── Step 2: Price changes ─────────────────────────────────────────────────────
@retry
def _fetch_price_changes(symbols):
    tickers_yf = [f"{s}.NS" for s in symbols]
    df = yf.download(
        tickers_yf,
        period="2d",
        interval="1d",
        group_by="ticker",
        auto_adjust=True,
        progress=False,
    )
    price_changes = {}
    for sym in symbols:
        try:
            closes = (
                df[f"{sym}.NS"]["Close"].dropna().values
                if len(symbols) > 1
                else df["Close"].dropna().values
            )
            price_changes[sym] = (
                round(((closes[-1] - closes[-2]) / closes[-2]) * 100, 2)
                if len(closes) >= 2 else None
            )
        except Exception:
            price_changes[sym] = None
    return price_changes


def get_price_changes(symbols, trade_date):
    log(f"Step 2 — Price data for {len(symbols)} stocks (Yahoo Finance)")
    key = f"price_changes_{trade_date}"
    cached = cache_load(key, ttl_sec=LIVE_TTL_SEC)
    if cached is not None:
        log(f"  → {sum(v is not None for v in cached.values())} stocks (from cache)")
        return cached
    price_changes = _fetch_price_changes(symbols)
    cache_save(key, price_changes)
    valid = sum(v is not None for v in price_changes.values())
    log(f"  → Price data fetched for {valid}/{len(symbols)} stocks")
    return price_changes


# ── Step 4: Bhav copy — PCR + liquidity ──────────────────────────────────────
@retry
def _fetch_bhavcopy(trade_date_str):
    url = f"https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{trade_date_str}_F_0000.csv.zip"
    r = requests.get(url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=30)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    return pd.read_csv(z.open(z.namelist()[0]))


def get_bhavcopy_data(trade_date):
    log(f"Step 4 — NSE F&O Bhav copy ({trade_date})")
    key = f"bhavcopy_{trade_date}"
    cached = cache_load(key)   # date-keyed, no TTL
    if cached is not None:
        pcr_map      = cached["pcr_map"]
        liquid_stocks = set(cached["liquid_stocks"])
        log(f"  → {len(pcr_map)} PCR entries, {len(liquid_stocks)} liquid stocks (from cache)")
        return pcr_map, liquid_stocks

    df = _fetch_bhavcopy(trade_date)

    # ── Liquidity: total option volume across all expiries ──
    opts = df[df["FinInstrmTp"] == "STO"][["TckrSymb", "TtlTradgVol"]].copy()
    opts["TtlTradgVol"] = pd.to_numeric(opts["TtlTradgVol"], errors="coerce").fillna(0)
    total_vol     = opts.groupby("TckrSymb")["TtlTradgVol"].sum()
    vol_threshold = total_vol.quantile(0.60)
    liquid_stocks = set(total_vol[total_vol >= vol_threshold].index)
    log(f"  → Liquidity threshold: {vol_threshold:,.0f} contracts | {len(liquid_stocks)} liquid stocks")

    # ── PCR: near-month expiry + ATM ±15% strikes ──
    full = df[df["FinInstrmTp"] == "STO"][
        ["TckrSymb", "OptnTp", "XpryDt", "StrkPric", "OpnIntrst", "UndrlygPric"]
    ].copy()
    for col in ["OpnIntrst", "StrkPric", "UndrlygPric"]:
        full[col] = pd.to_numeric(full[col], errors="coerce")
    full["OpnIntrst"] = full["OpnIntrst"].fillna(0)
    full["XpryDt"]    = pd.to_datetime(full["XpryDt"])

    near_expiry = full.groupby("TckrSymb")["XpryDt"].min()
    near = full[full.apply(lambda r: r["XpryDt"] == near_expiry.get(r["TckrSymb"]), axis=1)].copy()
    near = near[
        (near["StrkPric"] >= near["UndrlygPric"] * 0.85) &
        (near["StrkPric"] <= near["UndrlygPric"] * 1.15)
    ]

    call_oi    = near[near["OptnTp"] == "CE"].groupby("TckrSymb")["OpnIntrst"].sum()
    put_oi     = near[near["OptnTp"] == "PE"].groupby("TckrSymb")["OpnIntrst"].sum()
    pcr_map    = (put_oi / call_oi.replace(0, float("nan"))).dropna().round(2).to_dict()
    log(f"  → PCR calculated for {len(pcr_map)} stocks (near-month, ATM ±15%)")

    cache_save(key, {"pcr_map": pcr_map, "liquid_stocks": list(liquid_stocks)})
    return pcr_map, liquid_stocks


# ── Step 6: Market cap ────────────────────────────────────────────────────────
@retry
def _fetch_market_cap(symbol):
    fi = yf.Ticker(f"{symbol}.NS").fast_info
    return round(fi.market_cap / 1e7) if fi.market_cap else None


def get_market_caps(symbols, trade_date):
    log(f"Step 6 — Market cap for {len(symbols)} matched stocks (Yahoo Finance)")
    key = f"market_cap_{trade_date}"
    cached = cache_load(key) or {}   # date-keyed, partial dict OK
    result = {}
    to_fetch = [s for s in symbols if s not in cached]

    for sym in to_fetch:
        try:
            mcap = _fetch_market_cap(sym)
            cached[sym] = mcap
            log(f"  {sym:<15} ₹{mcap:,} Cr" if mcap else f"  {sym:<15} N/A")
        except Exception as e:
            cached[sym] = None
            log(f"  {sym:<15} !! {e}")

    if to_fetch:
        cache_save(key, cached)
    else:
        log("  → All from cache")

    for sym in symbols:
        result[sym] = cached.get(sym)
    return result


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
    key = f"fii_dii_{trade_date}"
    cached = cache_load(key)   # date-keyed, no TTL (stable once market closes)
    if cached is not None:
        log(f"  → FII net: ₹{cached.get('fii_net', 0):+,.2f} Cr  DII net: ₹{cached.get('dii_net', 0):+,.2f} Cr (cache)")
        return cached
    data = _fetch_fii_dii(session)
    cache_save(key, data)
    log(f"  → FII net: ₹{data.get('fii_net', 0):+,.2f} Cr  DII net: ₹{data.get('dii_net', 0):+,.2f} Cr")
    return data


def compute_fii_sentiment(lookback_days=5):
    """
    Read the last N fii_dii_*.json cache files and compute rolling FII net sentiment.
    Returns: dict with sentiment label, 5-day sum, and per-day records.
    """
    files = sorted(
        [f for f in os.listdir(CACHE_DIR) if f.startswith("fii_dii_") and f.endswith(".json")],
        reverse=True,
    )[:lookback_days]

    if not files:
        return {"sentiment": "UNKNOWN", "fii_nd_net": 0.0, "days": 0, "records": []}

    total, records = 0.0, []
    for fname in files:
        with open(os.path.join(CACHE_DIR, fname)) as f:
            entry = json.load(f)
        data    = entry.get("data", entry)   # unwrap cache wrapper
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
    log(f"  Filter: Price UP + OI >= +{MIN_OI_CHANGE_PCT}% + PCR >= {MIN_PCR} (ATM ±15%, near-month)")
    log(f"  Macro : FII {FII_LOOKBACK_DAYS}-day rolling  |  BEARISH<{FII_BEARISH_THRESH}Cr, CAUTIOUS<{FII_CAUTIOUS_THRESH}Cr")
    log(f"  Cache : {os.path.abspath(CACHE_DIR)}  |  Live TTL: {LIVE_TTL_SEC}s")
    log("=" * 65)

    cleanup_old_cache(days=30)

    session = init_session()

    # Step 7 — FII/DII (run first; builds rolling cache before sentiment check)
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
    log(f"  ── 5D FII Net: ₹{macro['fii_nd_net']:+,.2f} Cr  →  Sentiment: {macro['sentiment']} ──\n")

    if macro["sentiment"] == "BEARISH":
        log("  *** MACRO ALERT: FII aggressively SELLING — HIGH risk of false long signals ***")
    elif macro["sentiment"] == "CAUTIOUS":
        log("  **  MACRO CAUTION: FII net selling — verify each trade carefully  **")

    # Step 1
    oi_stocks = get_oi_spurt_stocks(session, trade_date)
    symbols   = [s["symbol"] for s in oi_stocks]
    oi_map    = {s["symbol"]: s for s in oi_stocks}

    # Step 2
    price_changes = get_price_changes(symbols, trade_date)

    # Step 3 — filter (no API, no cache needed)
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

    # Step 4
    try:
        pcr_map, liquid_stocks = get_bhavcopy_data(trade_date)
    except Exception as e:
        log(f"  !! Bhav copy failed after retries: {e}")
        pcr_map, liquid_stocks = {}, set()

    # Step 5 — filter (no API)
    log(f"Step 5 — PCR >= {MIN_PCR} + Liquid only")
    results = []
    for stock in long_buildup:
        sym    = stock["symbol"]
        pcr    = pcr_map.get(sym)
        liquid = sym in liquid_stocks
        pcr_str = f"{pcr:.2f}" if pcr is not None else "N/A"
        if pcr is not None and pcr >= MIN_PCR and liquid:
            results.append({**stock, "pcr": pcr})
            log(f"  ✓✓ {sym:<15} PCR: {pcr_str}  liquid: yes  → MATCH")
        else:
            reasons = []
            if pcr is None or pcr < MIN_PCR: reasons.append(f"PCR {pcr_str}")
            if not liquid: reasons.append("illiquid")
            log(f"     {sym:<15} PCR: {pcr_str}  liquid: {'yes' if liquid else 'no '}  → {', '.join(reasons)}")

    # Step 6
    mcap_map = {}
    if results:
        mcap_map = get_market_caps([r["symbol"] for r in results], trade_date)
        for r in results:
            r["market_cap_cr"] = mcap_map.get(r["symbol"])

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
        print(f"\n{'#':<4} {'Symbol':<15} {'Spot Price':>10} {'Price Chg%':>11} {'OI Chg%':>10} {'PCR':>7} {'Mkt Cap (Cr)':>14}  {'Macro':>9}")
        print("-" * 87)
        for idx, r in enumerate(results_sorted, 1):
            mcap  = f"₹{r['market_cap_cr']:>10,}" if r.get("market_cap_cr") else "          N/A"
            macro_col = f"[{icon}] {sentiment}"
            print(f"{idx:<4} {r['symbol']:<15} {r['spot_price']:>10.2f} {r['price_chg']:>+10.2f}% {r['oi_chg']:>+9.2f}% {r['pcr']:>7.2f} {mcap:>14}  {macro_col}")
        print()
        log(f"Total matched: {len(results)} stocks  |  FII 5D Net: ₹{macro['fii_nd_net']:+,.2f} Cr  [{icon}] {sentiment}")
    else:
        log("No stocks matched the criteria.")

    output = {
        "scan_time": datetime.now().isoformat(),
        "criteria": {"min_oi_chg_pct": MIN_OI_CHANGE_PCT, "min_pcr": MIN_PCR},
        "macro":    {"sentiment": sentiment, "fii_5d_net_cr": macro["fii_nd_net"], "days": macro["days"], "history": macro["records"]},
        "summary":  {"oi_spurt_stocks": len(oi_stocks), "long_buildup": len(long_buildup), "matched": len(results)},
        "results":  results,
    }
    with open("scan_results.json", "w") as f:
        json.dump(output, f, indent=2)
    log("Results saved → scan_results.json")
    log("=" * 82)


if __name__ == "__main__":
    scan()
