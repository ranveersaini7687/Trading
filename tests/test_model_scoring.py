#!/usr/bin/env python3
"""Regression tests for framework model scoring (doc examples)."""

import unittest

from model_scoring import score_signal


def _signal(symbol, price_chg, oi_chg, vol_ratio, pcr, spot_price, ema9):
    return {
        "symbol": symbol,
        "price_chg": price_chg,
        "oi_chg": oi_chg,
        "vol_ratio": vol_ratio,
        "pcr": pcr,
        "spot_price": spot_price,
        "ema9": ema9,
    }


class TestModelScoring(unittest.TestCase):
    TOL = 0.5

    def _assert_score(self, actual, expected):
        self.assertAlmostEqual(actual, expected, delta=self.TOL)

    def test_divislab_win_all_models_high(self):
        s = _signal("DIVISLAB", 3.61, 18.48, 3.19, 1.22, 1000, 990)
        r = score_signal(s)
        for key in ("model_a", "model_b", "model_c", "model_d", "model_e", "model_f"):
            self._assert_score(r[key], 100)
        self.assertTrue(r["model_f_pass"])
        self.assertEqual(r["suggested_action"], "full")

    def test_bajaj_auto_scores_without_pcr_gate(self):
        s = _signal("BAJAJ-AUTO", 2.50, 9.59, 2.74, 0.84, 1000, 990)
        r = score_signal(s)
        self._assert_score(r["model_a"], 94)
        self.assertGreater(r["composite_abc"], 70)
        self.assertTrue(r["model_f_pass"])

    def test_sonacoms_low_pcr_still_scores(self):
        s = _signal("SONACOMS", 2.12, 7.96, 1.27, 0.71, 683.4, 666.63)
        r = score_signal(s)
        self._assert_score(r["model_a"], 84.2)
        self.assertGreater(r["composite_abc"], 70)
        self.assertEqual(r["suggested_action"], "conditional")

    def test_phoenix_model_f_zero_others_high(self):
        s = _signal("PHOENIXLTD", 2.58, 14.04, 2.73, 1.00, 1000, 990)
        r = score_signal(s)
        self._assert_score(r["model_a"], 94)
        self._assert_score(r["model_b"], 88)
        self._assert_score(r["model_c"], 96)
        self._assert_score(r["model_d"], 93.4)
        self._assert_score(r["model_e"], 93)
        self._assert_score(r["model_f"], 90)
        self.assertTrue(r["model_f_pass"])

    def test_amber_model_f_zero_abc_valid(self):
        s = _signal("AMBER", 2.03, 12.22, 1.61, 0.98, 1000, 990)
        r = score_signal(s)
        self._assert_score(r["model_a"], 86.9)
        self._assert_score(r["model_b"], 80.9)
        self._assert_score(r["model_c"], 74.6)
        self._assert_score(r["model_d"], 81.3)
        self._assert_score(r["model_e"], 85.9)
        self._assert_score(r["model_f"], 82.9)
        self.assertTrue(r["model_f_pass"])
        self.assertEqual(r["suggested_action"], "conditional")

    def test_quality_ratio(self):
        s = _signal("TEST", 2.0, 10.0, 2.0, 1.0, 100, 90)
        r = score_signal(s)
        self.assertEqual(r["quality_ratio"], 5.0)


if __name__ == "__main__":
    unittest.main()
