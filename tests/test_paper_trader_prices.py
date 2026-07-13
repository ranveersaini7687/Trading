#!/usr/bin/env python3
"""Tests for unified entry pricing and Excel trade rows."""

import unittest
from unittest.mock import patch

from paper_trader import _trades_rows, _empty_portfolio, _open_positions


class TradesRowsTest(unittest.TestCase):

    def test_open_row_uses_cmp_not_exit(self):
        portfolio = _empty_portfolio()
        portfolio["positions"]["TEST"] = {
            "entry_price": 100.0,
            "quantity": 10,
            "invested": 1000.0,
            "entry_date": "2026-07-13",
            "stop_loss": 99.0,
            "target": 102.0,
            "price_chg": 1.0,
            "oi_chg": 2.0,
            "vol_ratio": 1.5,
            "pcr": 0.9,
            "sector": "-",
            "macro_entry": "NEUTRAL",
        }
        rows = _trades_rows(portfolio, {"TEST": 101.0})
        open_row = next(r for r in rows if r["Status"] == "OPEN")
        self.assertEqual(open_row["Exit ₹"], "-")
        self.assertEqual(open_row["CMP ₹"], 101.0)
        self.assertEqual(open_row["P&L ₹"], 10.0)

    def test_closed_row_uses_exit_price(self):
        portfolio = _empty_portfolio()
        portfolio["closed_trades"] = [{
            "symbol": "TEST",
            "entry_date": "2026-07-10",
            "exit_date": "2026-07-11",
            "entry_price": 100.0,
            "exit_price": 98.0,
            "quantity": 10,
            "pnl_abs": -20.0,
            "pnl_pct": -2.0,
            "reason": "SL HIT",
            "macro_entry": "NEUTRAL",
        }]
        rows = _trades_rows(portfolio, {})
        closed = rows[0]
        self.assertEqual(closed["Exit ₹"], 98.0)
        self.assertEqual(closed["CMP ₹"], "-")


class OpenPositionsEntryPriceTest(unittest.TestCase):

    @patch("paper_trader.fetch_prices")
    def test_uses_live_ltp_over_scanner_spot(self, mock_fetch):
        mock_fetch.return_value = {"ABC": 105.0}
        portfolio = _empty_portfolio()
        signals = [{
            "symbol": "ABC",
            "spot_price": 100.0,
            "price_chg": 1.0,
            "oi_chg": 2.0,
            "vol_ratio": 1.5,
            "pcr": 0.9,
        }]
        entered = _open_positions(
            portfolio, signals, "NEUTRAL", 0.0, "2026-07-13",
        )
        self.assertEqual(entered, {"ABC"})
        self.assertEqual(portfolio["positions"]["ABC"]["entry_price"], 105.0)


if __name__ == "__main__":
    unittest.main()
