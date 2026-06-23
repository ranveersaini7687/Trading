#!/usr/bin/env python3
"""
Paper Trader — Equity cash simulation on Long Buildup scanner signals.

Capital  : ₹10,00,000
Sizing   : ₹1,00,000 per trade  (max 10 positions)
Stop-loss: -1%  from entry
Target   : +2%  from entry
Exit also: if stock falls out of scanner signals (long buildup gone)

Run daily:
  python3 long_buildup_scanner.py   → refresh signals
  python3 paper_trader.py           → update P&L + Excel report
"""

import json
import os
import time
import pandas as pd
import yfinance as yf
from datetime import datetime
from openpyxl import load_workbook
from openpyxl.styles import PatternFill, Font, Alignment
from openpyxl.utils import get_column_letter

# ── Config ────────────────────────────────────────────────────────────────────
PORTFOLIO_FILE  = "paper_portfolio.json"
SCAN_FILE       = "scan_results.json"
EXCEL_FILE      = "trade_log.xlsx"

TOTAL_CAPITAL   = 1_000_000      # ₹10,00,000
MAX_POSITIONS   = 10              # up to 10 slots × ₹1L
ALLOC_PER_TRADE = 100_000         # ₹1,00,000 per trade
STOP_LOSS_PCT   = 1.0             # -1%
TARGET_PCT      = 2.0             # +2%


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ── Portfolio persistence ─────────────────────────────────────────────────────
def load_portfolio():
    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE) as f:
            return json.load(f)
    log("  [portfolio] No existing portfolio — starting fresh with ₹10,00,000")
    return {
        "total_capital":  TOTAL_CAPITAL,
        "cash":           TOTAL_CAPITAL,
        "positions":      {},
        "closed_trades":  [],
        "created":        datetime.now().isoformat(),
    }


def save_portfolio(portfolio):
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(portfolio, f, indent=2)


# ── Price fetch (batched) ─────────────────────────────────────────────────────
def fetch_prices(symbols):
    if not symbols:
        return {}
    tickers = [f"{s}.NS" for s in symbols]
    try:
        df = yf.download(tickers, period="1d", interval="1d",
                         group_by="ticker", auto_adjust=True, progress=False)
        prices = {}
        for sym in symbols:
            try:
                col  = df[f"{sym}.NS"]["Close"] if len(symbols) > 1 else df["Close"]
                vals = col.dropna().values
                prices[sym] = round(float(vals[-1]), 2) if len(vals) else None
            except Exception:
                prices[sym] = None
        return prices
    except Exception as e:
        log(f"  !! Price fetch failed: {e}")
        return {s: None for s in symbols}


# ── Scanner results ───────────────────────────────────────────────────────────
def load_signals():
    if not os.path.exists(SCAN_FILE):
        log(f"  !! {SCAN_FILE} not found — run long_buildup_scanner.py first")
        return [], "UNKNOWN"
    with open(SCAN_FILE) as f:
        data = json.load(f)
    macro = data.get("macro", {}).get("sentiment", "UNKNOWN")
    return data.get("results", []), macro


# ── Excel report ──────────────────────────────────────────────────────────────
GREEN_FILL = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
RED_FILL   = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
GREY_FILL  = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")
HDR_FONT   = Font(bold=True)
CENTER     = Alignment(horizontal="center")


def _color_pnl_col(ws, col_letter):
    for row in ws.iter_rows(min_row=2, min_col=ws[col_letter + "1"].column,
                             max_col=ws[col_letter + "1"].column):
        for cell in row:
            if isinstance(cell.value, (int, float)):
                cell.fill = GREEN_FILL if cell.value >= 0 else RED_FILL


def _autofit(ws):
    for col in ws.columns:
        max_len = max((len(str(c.value)) if c.value else 0) for c in col)
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(max_len + 4, 30)


def _header_row(ws):
    for cell in ws[1]:
        cell.font = HDR_FONT
        cell.fill = GREY_FILL
        cell.alignment = CENTER


def generate_excel(portfolio, all_prices):
    trades     = portfolio.get("closed_trades", [])
    positions  = portfolio.get("positions", {})

    with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl") as writer:

        # ── Sheet 1: All Trades ───────────────────────────────────────────────
        if trades:
            df = pd.DataFrame(trades)[
                ["symbol", "entry_date", "exit_date", "entry_price",
                 "exit_price", "quantity", "pnl_abs", "pnl_pct", "reason", "macro_entry"]
            ].copy()
            df.columns = ["Symbol", "Entry Date", "Exit Date", "Entry ₹",
                          "Exit ₹", "Qty", "P&L ₹", "P&L %", "Exit Reason", "Macro"]
            df.sort_values("Exit Date", ascending=False, inplace=True)
        else:
            df = pd.DataFrame(columns=["Symbol", "Entry Date", "Exit Date", "Entry ₹",
                                        "Exit ₹", "Qty", "P&L ₹", "P&L %", "Exit Reason", "Macro"])
        df.to_excel(writer, sheet_name="All Trades", index=False)

        # ── Sheet 2: Open Positions ───────────────────────────────────────────
        rows = []
        for sym, pos in positions.items():
            curr    = all_prices.get(sym) or pos["entry_price"]
            pnl     = round((curr - pos["entry_price"]) * pos["quantity"], 2)
            pnl_pct = round((curr - pos["entry_price"]) / pos["entry_price"] * 100, 2)
            rows.append({
                "Symbol":        sym,
                "Entry Date":    pos["entry_date"],
                "Entry ₹":       pos["entry_price"],
                "CMP ₹":         curr,
                "Qty":           pos["quantity"],
                "Invested ₹":    pos["invested"],
                "Unrealised ₹":  pnl,
                "P&L %":         pnl_pct,
                "SL ₹":          pos["stop_loss"],
                "Target ₹":      pos["target"],
                "Macro":         pos.get("macro_entry", "?"),
            })
        pd.DataFrame(rows).to_excel(writer, sheet_name="Open Positions", index=False)

        # ── Sheet 3: Daily P&L ────────────────────────────────────────────────
        if trades:
            df2 = pd.DataFrame(trades)
            daily = (df2.groupby("exit_date")
                        .agg(Trades=("symbol", "count"),
                             PnL=("pnl_abs", "sum"),
                             Wins=("pnl_abs", lambda x: (x > 0).sum()),
                             Losses=("pnl_abs", lambda x: (x <= 0).sum()))
                        .reset_index()
                        .sort_values("exit_date"))
            daily.columns = ["Date", "Trades", "P&L ₹", "Wins", "Losses"]
            daily["Win Rate %"] = (daily["Wins"] / daily["Trades"] * 100).round(1)
            daily["P&L ₹"]      = daily["P&L ₹"].round(2)
            daily["Cumul P&L ₹"] = daily["P&L ₹"].cumsum().round(2)
        else:
            daily = pd.DataFrame(columns=["Date", "Trades", "P&L ₹",
                                           "Wins", "Losses", "Win Rate %", "Cumul P&L ₹"])
        daily.to_excel(writer, sheet_name="Daily P&L", index=False)

        # ── Sheet 4: Weekly P&L ───────────────────────────────────────────────
        if trades:
            df3 = pd.DataFrame(trades)
            df3["exit_date"] = pd.to_datetime(df3["exit_date"])
            df3["Week"] = df3["exit_date"].dt.strftime("W%V %Y")
            df3["wk_sort"] = df3["exit_date"].dt.strftime("%Y-W%V")
            weekly = (df3.groupby(["wk_sort", "Week"])
                         .agg(Trades=("symbol", "count"),
                              PnL=("pnl_abs", "sum"),
                              Wins=("pnl_abs", lambda x: (x > 0).sum()),
                              Losses=("pnl_abs", lambda x: (x <= 0).sum()))
                         .reset_index()
                         .sort_values("wk_sort")
                         .drop("wk_sort", axis=1))
            weekly.columns = ["Week", "Trades", "P&L ₹", "Wins", "Losses"]
            weekly["Win Rate %"] = (weekly["Wins"] / weekly["Trades"] * 100).round(1)
            weekly["P&L ₹"]      = weekly["P&L ₹"].round(2)
            weekly["Cumul P&L ₹"] = weekly["P&L ₹"].cumsum().round(2)
        else:
            weekly = pd.DataFrame(columns=["Week", "Trades", "P&L ₹",
                                            "Wins", "Losses", "Win Rate %", "Cumul P&L ₹"])
        weekly.to_excel(writer, sheet_name="Weekly P&L", index=False)

        # ── Sheet 5: Monthly P&L ──────────────────────────────────────────────
        if trades:
            df4 = pd.DataFrame(trades)
            df4["exit_date"] = pd.to_datetime(df4["exit_date"])
            df4["Month"]     = df4["exit_date"].dt.strftime("%b %Y")
            df4["mo_sort"]   = df4["exit_date"].dt.strftime("%Y-%m")
            monthly = (df4.groupby(["mo_sort", "Month"])
                          .agg(Trades=("symbol", "count"),
                               PnL=("pnl_abs", "sum"),
                               Wins=("pnl_abs", lambda x: (x > 0).sum()),
                               Losses=("pnl_abs", lambda x: (x <= 0).sum()))
                          .reset_index()
                          .sort_values("mo_sort")
                          .drop("mo_sort", axis=1))
            monthly.columns = ["Month", "Trades", "P&L ₹", "Wins", "Losses"]
            monthly["Win Rate %"] = (monthly["Wins"] / monthly["Trades"] * 100).round(1)
            monthly["P&L ₹"]      = monthly["P&L ₹"].round(2)
            monthly["Cumul P&L ₹"] = monthly["P&L ₹"].cumsum().round(2)
        else:
            monthly = pd.DataFrame(columns=["Month", "Trades", "P&L ₹",
                                             "Wins", "Losses", "Win Rate %", "Cumul P&L ₹"])
        monthly.to_excel(writer, sheet_name="Monthly P&L", index=False)

    # ── Apply colour + formatting ─────────────────────────────────────────────
    wb = load_workbook(EXCEL_FILE)

    for sname in ["All Trades", "Open Positions", "Daily P&L", "Weekly P&L", "Monthly P&L"]:
        ws = wb[sname]
        _header_row(ws)
        _autofit(ws)
        # Colour P&L columns
        for cell in ws[1]:
            if cell.value and "P&L ₹" in str(cell.value) and "Cumul" not in str(cell.value):
                _color_pnl_col(ws, get_column_letter(cell.column))

    wb.save(EXCEL_FILE)
    log(f"  Trade log saved → {EXCEL_FILE}  (sheets: All Trades | Open Positions | Daily | Weekly | Monthly)")


# ── Main ──────────────────────────────────────────────────────────────────────
def run(intraday_only=False):
    portfolio = load_portfolio()
    signals, macro = load_signals()
    signal_map = {r["symbol"]: r for r in signals}
    today      = datetime.now().strftime("%Y-%m-%d")

    log("=" * 72)
    log("  PAPER TRADER — ₹10,00,000 Virtual Portfolio")
    log(f"  Date       : {datetime.now().strftime('%d %b %Y %H:%M IST')}")
    log(f"  Cash       : ₹{portfolio['cash']:>10,.0f}  |  Open: {len(portfolio['positions'])} positions")
    log(f"  FII Macro  : {macro}  |  SL: -{STOP_LOSS_PCT}%  Target: +{TARGET_PCT}%  Alloc: ₹{ALLOC_PER_TRADE:,.0f}/trade")
    log("=" * 72)

    # ── Step 1: Fetch current prices for all open positions ───────────────────
    open_syms   = list(portfolio["positions"].keys())
    curr_prices = fetch_prices(open_syms) if open_syms else {}

    # ── Step 2: Check exits ───────────────────────────────────────────────────
    log("\n  Checking open positions...")
    to_close = []

    if not open_syms:
        log("  (no open positions)")

    for sym, pos in portfolio["positions"].items():
        curr = curr_prices.get(sym)
        if curr is None:
            log(f"  ? {sym:<15} — price unavailable, skipping exit check")
            continue

        pnl_pct = round((curr - pos["entry_price"]) / pos["entry_price"] * 100, 2)
        pnl_abs = round((curr - pos["entry_price"]) * pos["quantity"], 2)

        reason = None
        if curr <= pos["stop_loss"]:
            reason = "SL HIT"
        elif curr >= pos["target"]:
            reason = "TARGET HIT"
        elif signals and sym not in signal_map:
            # Only exit on SIGNAL GONE when scanner actually returned results.
            # Empty signals means bhav copy unavailable intraday — not a real exit.
            reason = "SIGNAL GONE"

        sign   = "+" if pnl_abs >= 0 else ""
        marker = "✗" if reason else " "
        log(f"  {marker} {sym:<15} CMP ₹{curr:>8,.2f}  Entry ₹{pos['entry_price']:>8,.2f}"
            f"  {sign}{pnl_pct:.2f}%  ₹{sign}{pnl_abs:>8,.0f}"
            f"{'  → ' + reason if reason else ''}")

        if reason:
            to_close.append((sym, curr, pnl_abs, pnl_pct, reason))

    for sym, exit_px, pnl_abs, pnl_pct, reason in to_close:
        pos = portfolio["positions"].pop(sym)
        portfolio["cash"] += round(exit_px * pos["quantity"], 2)
        portfolio["closed_trades"].append({
            "symbol":      sym,
            "entry_date":  pos["entry_date"],
            "exit_date":   today,
            "entry_price": pos["entry_price"],
            "exit_price":  exit_px,
            "quantity":    pos["quantity"],
            "pnl_abs":     pnl_abs,
            "pnl_pct":     pnl_pct,
            "reason":      reason,
            "macro_entry": pos.get("macro_entry", "UNKNOWN"),
        })
        sign = "+" if pnl_abs >= 0 else ""
        log(f"  ✗ CLOSED {sym:<14} Exit ₹{exit_px:.2f}  P&L ₹{sign}{pnl_abs:,.0f} ({sign}{pnl_pct:.2f}%)  [{reason}]")

    # ── Step 3: Open new positions ────────────────────────────────────────────
    if intraday_only:
        log("  (intraday mode — skipping new entries, SL/target check only)")
        save_portfolio(portfolio)
        generate_excel(portfolio, curr_prices)
        log("=" * 72)
        return

    already_open  = set(portfolio["positions"].keys())
    traded_today  = {t["symbol"] for t in portfolio["closed_trades"] if t["exit_date"] == today}
    skipped_today = [r["symbol"] for r in signals if r["symbol"] in traded_today]
    new_signals   = [r for r in signals if r["symbol"] not in already_open and r["symbol"] not in traded_today]
    slots_free    = MAX_POSITIONS - len(portfolio["positions"])

    log(f"\n  Signals: {len(signals)}  |  New: {len(new_signals)}  |  Slots free: {slots_free}  |  Cash: ₹{portfolio['cash']:,.0f}")
    if skipped_today:
        log(f"  Skipped (already traded today): {', '.join(skipped_today)}")

    if macro == "BEARISH":
        log("  *** MACRO BEARISH — FII aggressively selling. No new entries today. ***")
        new_signals = []
    elif macro == "CAUTIOUS":
        log("  **  MACRO CAUTIOUS — FII net selling. Entering with reduced conviction. **")

    for sig in new_signals[:slots_free]:
        sym      = sig["symbol"]
        entry_px = sig["spot_price"]
        if entry_px <= 0:
            continue
        allocated = min(ALLOC_PER_TRADE, portfolio["cash"])
        if allocated < entry_px:
            log(f"  !! {sym}: not enough cash (need ₹{entry_px:.2f}, have ₹{portfolio['cash']:,.0f})")
            continue
        qty      = int(allocated // entry_px)
        if qty == 0:
            continue
        invested = round(qty * entry_px, 2)
        sl_px    = round(entry_px * (1 - STOP_LOSS_PCT / 100), 2)
        tgt_px   = round(entry_px * (1 + TARGET_PCT  / 100), 2)

        portfolio["cash"] -= invested
        portfolio["positions"][sym] = {
            "entry_price": entry_px,
            "quantity":    qty,
            "invested":    invested,
            "entry_date":  today,
            "stop_loss":   sl_px,
            "target":      tgt_px,
            "pcr":         sig.get("pcr"),
            "oi_chg":      sig.get("oi_chg"),
            "macro_entry": macro,
        }
        log(f"  + OPEN  {sym:<14} {qty} sh @ ₹{entry_px:,.2f}"
            f"  invested ₹{invested:,.0f}  SL ₹{sl_px:.2f}  T ₹{tgt_px:.2f}  [{macro}]")

    # ── Step 4: Refresh prices for portfolio value ────────────────────────────
    all_syms   = list(portfolio["positions"].keys())
    all_prices = fetch_prices(all_syms) if all_syms else {}
    # Merge with prices fetched earlier (avoid double-fetching)
    all_prices = {**curr_prices, **all_prices}

    unrealised  = sum(
        (all_prices.get(s, p["entry_price"]) - p["entry_price"]) * p["quantity"]
        for s, p in portfolio["positions"].items()
    )
    invested_val = sum(p["invested"] for p in portfolio["positions"].values())
    portfolio_val = portfolio["cash"] + invested_val + unrealised
    total_pnl     = portfolio_val - TOTAL_CAPITAL
    total_pnl_pct = round(total_pnl / TOTAL_CAPITAL * 100, 2)
    closed_pnl    = sum(t["pnl_abs"] for t in portfolio["closed_trades"])
    wins          = [t for t in portfolio["closed_trades"] if t["pnl_abs"] > 0]
    losses        = [t for t in portfolio["closed_trades"] if t["pnl_abs"] <= 0]
    win_rate      = round(len(wins) / len(portfolio["closed_trades"]) * 100) if portfolio["closed_trades"] else 0

    # ── Step 5: Print open positions ──────────────────────────────────────────
    if portfolio["positions"]:
        log("\n  Open Positions:")
        print(f"\n  {'Symbol':<15} {'Entry ₹':>9} {'CMP ₹':>9} {'Qty':>6}"
              f" {'SL ₹':>9} {'Target ₹':>10} {'P&L ₹':>10} {'P&L%':>7}  Macro")
        print("  " + "-" * 90)
        for sym, pos in portfolio["positions"].items():
            curr    = all_prices.get(sym) or pos["entry_price"]
            pnl     = round((curr - pos["entry_price"]) * pos["quantity"], 2)
            pnl_pct = round((curr - pos["entry_price"]) / pos["entry_price"] * 100, 2)
            sign    = "+" if pnl >= 0 else ""
            print(f"  {sym:<15} {pos['entry_price']:>9.2f} {curr:>9.2f} {pos['quantity']:>6}"
                  f" {pos['stop_loss']:>9.2f} {pos['target']:>10.2f}"
                  f" {sign}{pnl:>9,.0f} {sign}{pnl_pct:>6.2f}%  {pos.get('macro_entry','?')}")
        print()

    # ── Step 6: Closed trades table ───────────────────────────────────────────
    if portfolio["closed_trades"]:
        log("  Closed Trades:")
        print(f"\n  {'Symbol':<15} {'Entry':>10} {'Exit':>10} {'Qty':>6}"
              f" {'P&L ₹':>11} {'P&L%':>8}  Reason")
        print("  " + "-" * 72)
        for t in sorted(portfolio["closed_trades"], key=lambda x: x["exit_date"], reverse=True):
            sign = "+" if t["pnl_abs"] >= 0 else ""
            print(f"  {t['symbol']:<15} {t['entry_price']:>10.2f} {t['exit_price']:>10.2f}"
                  f" {t['quantity']:>6} {sign}{t['pnl_abs']:>10,.0f} {sign}{t['pnl_pct']:>7.2f}%  {t['reason']}")
        print()

    # ── Step 7: Summary ───────────────────────────────────────────────────────
    sign = "+" if total_pnl >= 0 else ""
    log("=" * 72)
    log("  SUMMARY")
    log(f"  Portfolio Value : ₹{portfolio_val:>12,.0f}   ({sign}₹{total_pnl:,.0f}  {sign}{total_pnl_pct:.2f}%)")
    log(f"  Cash Available  : ₹{portfolio['cash']:>12,.0f}")
    log(f"  Invested        : ₹{invested_val:>12,.0f}   ({len(portfolio['positions'])} positions open)")
    log(f"  Unrealised P&L  : ₹{'+' if unrealised >= 0 else ''}{unrealised:>11,.0f}")
    log(f"  Realised P&L    : ₹{'+' if closed_pnl >= 0 else ''}{closed_pnl:>11,.0f}   ({len(wins)}W / {len(losses)}L  win rate {win_rate}%)")
    log("=" * 72)

    save_portfolio(portfolio)
    generate_excel(portfolio, all_prices)
    log("=" * 72)


if __name__ == "__main__":
    run()
