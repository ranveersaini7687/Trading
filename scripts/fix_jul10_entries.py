#!/usr/bin/env python3
"""One-off: correct Jul 10 baseline entries to live LTP from eod_confirm_log."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from paper_trader import (
    ALLOC_PER_TRADE,
    CONFIRM_PORTFOLIO_FILE,
    PORTFOLIO_FILE,
    STOP_LOSS_PCT,
    TARGET_PCT,
    load_portfolio,
    refresh_excel,
    save_portfolio,
)

ENTRY_DATE = "2026-07-10"
SYMBOLS = ("KALYANKJIL", "OFSS", "GODREJPROP")

# Live LTP at confirm time (15:31:48) — same batch used for correct entries
CANONICAL_ENTRY = {
    "KALYANKJIL": 476.15,
    "OFSS": 11651.0,
    "GODREJPROP": 2133.0,
}


def _position_fields(entry_px, qty, entry_date=ENTRY_DATE, **extra):
    invested = round(qty * entry_px, 2)
    return {
        "entry_price": entry_px,
        "quantity": qty,
        "invested": invested,
        "entry_date": entry_date,
        "stop_loss": round(entry_px * (1 - STOP_LOSS_PCT / 100), 2),
        "target": round(entry_px * (1 + TARGET_PCT / 100), 2),
        **extra,
    }


def _recalc_closed(trade, entry_px):
    qty = int(min(ALLOC_PER_TRADE, ALLOC_PER_TRADE) // entry_px)
    qty = trade["quantity"] if abs(trade["quantity"] * entry_px - ALLOC_PER_TRADE) < abs(qty * entry_px - ALLOC_PER_TRADE) else qty
    # Prefer standard alloc sizing at corrected entry
    qty = int(ALLOC_PER_TRADE // entry_px)
    exit_px = trade["exit_price"]
    pnl_abs = round((exit_px - entry_px) * qty, 2)
    pnl_pct = round((exit_px - entry_px) / entry_px * 100, 2)
    old_pnl = trade["pnl_abs"]
    trade.update({
        "entry_price": entry_px,
        "quantity": qty,
        "pnl_abs": pnl_abs,
        "pnl_pct": pnl_pct,
    })
    return pnl_abs - old_pnl


def fix_baseline(portfolio):
    cash_delta = 0.0

    for sym in SYMBOLS:
        entry_px = CANONICAL_ENTRY[sym]
        pos = portfolio["positions"].get(sym)
        if pos and pos.get("entry_date") == ENTRY_DATE:
            old_invested = pos["invested"]
            qty = int(ALLOC_PER_TRADE // entry_px)
            extra = {k: pos[k] for k in pos if k not in (
                "entry_price", "quantity", "invested", "stop_loss", "target",
            )}
            portfolio["positions"][sym] = _position_fields(entry_px, qty, **extra)
            cash_delta += old_invested - portfolio["positions"][sym]["invested"]
            print(f"  OPEN {sym}: entry → ₹{entry_px:,.2f}  qty {qty}")

    for trade in portfolio["closed_trades"]:
        if trade["symbol"] in SYMBOLS and trade.get("entry_date") == ENTRY_DATE:
            entry_px = CANONICAL_ENTRY[trade["symbol"]]
            old_entry = trade["entry_price"]
            if abs(old_entry - entry_px) < 0.01:
                continue
            cash_delta += _recalc_closed(trade, entry_px)
            print(
                f"  CLOSED {trade['symbol']}: entry ₹{old_entry:,.2f} → ₹{entry_px:,.2f}  "
                f"qty {trade['quantity']}  P&L ₹{trade['pnl_abs']:,.2f}"
            )

    portfolio["cash"] = round(portfolio["cash"] + cash_delta, 2)
    return portfolio


def main():
    base = load_portfolio(PORTFOLIO_FILE)
    print("Fixing baseline portfolio...")
    fix_baseline(base)
    save_portfolio(base, PORTFOLIO_FILE)
    print(f"  cash → ₹{base['cash']:,.2f}")

    print("Regenerating trade_log.xlsx...")
    refresh_excel()
    print("Done.")


if __name__ == "__main__":
    main()
