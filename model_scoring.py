#!/usr/bin/env python3
"""
Framework scoring models A–F for Long Buildup signals.

Models A–E: piecewise price/OI/vol weights only (no extra gates beyond live scanner).
Model F: same weights (30/50/20) — institutional OI floor removed; live filters suffice.
"""

MODEL_F_WEIGHTS = (0.30, 0.50, 0.20)


def score_price(pct):
    if pct is None:
        return 0
    if pct < 2:
        return 50
    if pct <= 6:
        return 100
    return 60


def score_oi(pct):
    if pct is None:
        return 0
    return 80 if pct <= 15 else 100


def score_vol(ratio):
    if ratio is None:
        return 0
    return min(100, round(ratio * 40, 2))


def score_quality(oi_pct, price_pct):
    if not price_pct or price_pct <= 0 or oi_pct is None:
        return 0
    ratio = oi_pct / price_pct
    if ratio >= 5:
        return 100
    if ratio >= 3:
        return 80
    if ratio >= 2:
        return 65
    if ratio >= 1:
        return 50
    return 20


def quality_ratio(oi_pct, price_pct):
    if not price_pct or price_pct <= 0 or oi_pct is None:
        return None
    return round(oi_pct / price_pct, 2)


def _weighted(sp, soi, sv, w_price, w_oi, w_vol, sq=None, w_quality=0):
    total = sp * w_price + soi * w_oi + sv * w_vol
    if sq is not None and w_quality:
        total += sq * w_quality
    return round(total, 1)


def suggested_action(composite_abc, model_f_pass):
    """Informational execution matrix from framework doc."""
    if composite_abc is None or composite_abc < 70:
        return "skip"
    if composite_abc >= 85:
        return "full" if model_f_pass else "half"
    if composite_abc >= 70 and model_f_pass:
        return "conditional"
    return "skip"


def score_signal(signal):
    """
    Compute model scores and derived analytics for a scanner signal.

    Expected keys: price_chg, oi_chg, vol_ratio (pcr/ema used by live scanner only).
    """
    price_chg = signal.get("price_chg")
    oi_chg = signal.get("oi_chg")
    vol_ratio = signal.get("vol_ratio")

    qr = quality_ratio(oi_chg, price_chg)

    if price_chg is None or oi_chg is None or vol_ratio is None:
        return {
            "quality_ratio": qr,
            "model_a": 0,
            "model_b": 0,
            "model_c": 0,
            "model_d": 0,
            "model_e": 0,
            "model_f": 0,
            "composite_abc": 0,
            "model_f_pass": False,
            "suggested_action": "skip",
        }

    sp = score_price(price_chg)
    soi = score_oi(oi_chg)
    sv = score_vol(vol_ratio)
    sq = score_quality(oi_chg, price_chg)

    model_a = _weighted(sp, soi, sv, 0.50, 0.30, 0.20)
    model_b = _weighted(sp, soi, sv, 0.20, 0.60, 0.20)
    model_c = _weighted(sp, soi, sv, 0.20, 0.20, 0.60)
    model_d = _weighted(sp, soi, sv, 0.33, 0.33, 0.34)
    model_e = _weighted(sp, soi, sv, 0.25, 0.35, 0.20, sq=sq, w_quality=0.20)
    model_f = _weighted(sp, soi, sv, *MODEL_F_WEIGHTS)
    model_f_pass = model_f > 0

    composite_abc = round((model_a + model_b + model_c) / 3, 1)
    action = suggested_action(composite_abc, model_f_pass)

    return {
        "quality_ratio": qr,
        "model_a": model_a,
        "model_b": model_b,
        "model_c": model_c,
        "model_d": model_d,
        "model_e": model_e,
        "model_f": model_f,
        "composite_abc": composite_abc,
        "model_f_pass": model_f_pass,
        "suggested_action": action,
    }


def attach_scores(signal, nifty_chg_pct=None):
    """Merge liquidity/model fields onto a signal dict."""
    out = {**signal, **score_signal(signal)}
    if nifty_chg_pct is not None:
        out["nifty_chg_pct"] = nifty_chg_pct
    return out
