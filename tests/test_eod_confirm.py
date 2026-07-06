#!/usr/bin/env python3
"""Unit tests for EOD confirm intraday filters."""

import unittest

from eod_confirm import evaluate_intraday, MIN_VOLUME_RATIO


class EvaluateIntradayTest(unittest.TestCase):

    def test_pass_green_candle_with_volume(self):
        quote = {"ltp": 105, "open": 100, "high": 106, "low": 99, "prev_close": 98, "volume": 2000}
        result = evaluate_intraday(quote, avg_vol=1000, scan_vol_ratio=1.8)
        self.assertTrue(result["vol_ok"])
        self.assertTrue(result["close_gt_open"])
        self.assertTrue(result["confirm_pass"])

    def test_fail_weak_close(self):
        quote = {"ltp": 99, "open": 100, "high": 101, "low": 98, "prev_close": 102, "volume": 2000}
        result = evaluate_intraday(quote, avg_vol=1000)
        self.assertTrue(result["vol_ok"])
        self.assertFalse(result["close_gt_open"])
        self.assertFalse(result["near_day_high"])
        self.assertFalse(result["new_high"])
        self.assertFalse(result["confirm_pass"])

    def test_pass_near_day_high(self):
        quote = {"ltp": 99.6, "open": 100, "high": 100, "low": 90, "prev_close": 95, "volume": 3000}
        result = evaluate_intraday(quote, avg_vol=1000)
        self.assertTrue(result["near_day_high"])
        self.assertTrue(result["confirm_pass"])

    def test_fail_low_volume(self):
        quote = {"ltp": 105, "open": 100, "high": 106, "low": 99, "prev_close": 98, "volume": 1000}
        result = evaluate_intraday(quote, avg_vol=1000)
        self.assertFalse(result["vol_ok"])
        self.assertFalse(result["confirm_pass"])
        self.assertIn(str(MIN_VOLUME_RATIO), result["fail_reason"])


if __name__ == "__main__":
    unittest.main()
