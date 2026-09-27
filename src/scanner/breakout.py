"""Breakout and pre-breakout (tight base near highs) scanners."""
from __future__ import annotations

import pandas as pd

from src.models import Evidence, ScanResult


def _trend_ok(last: pd.Series) -> bool:
    return bool(last["Close"] > last["EMA50"] > last["EMA200"])


DEFAULT_GRADES = {
    "strong_fresh": {"max_pct_above": 1.0, "min_volume_x": 5.0, "max_rsi": 70},
    "fresh": {"max_pct_above": 2.0, "min_volume_x": 5.0, "max_rsi": 70},
    "solid": {"max_pct_above": 3.0, "min_volume_x": 3.0, "max_rsi": 75},
    "extended_pct": 5.0,
    "extended_rsi": 75,
}


def grade_breakout(close: float, level: float, rsi: float, volume_x: float, grades: dict | None = None) -> str:
    """stock-2 setup grade relative to the breakout level.

    Strong Fresh ≤1% above, vol ≥5×, RSI <70 · Fresh ≤2%, vol ≥5×, RSI <70 · Solid ≤3%, vol ≥3×, RSI <75
    Extended >5% above or RSI ≥75 · Failed = back below the level.
    A breakout that fits none of these (e.g. 1.5% above on 2× volume) is "Unconfirmed" —
    stock-2 lumped it into Extended, which mislabels a low-volume breakout as over-extended.
    """
    g = {**DEFAULT_GRADES, **(grades or {})}
    if not level or level != level:
        return ""
    if close < level:
        return "Failed"
    pct = (close / level - 1) * 100
    if pct > g["extended_pct"] or rsi >= g["extended_rsi"]:
        return "Extended"
    for key, label in (("strong_fresh", "Strong Fresh"), ("fresh", "Fresh"), ("solid", "Solid")):
        r = g[key]
        if pct <= r["max_pct_above"] and volume_x >= r["min_volume_x"] and rsi < r["max_rsi"]:
            return label
    return "Unconfirmed"


def live_status(close: float, prev_close: float, level: float) -> str:
    """stock-2 live status: Held / Slipped (down on the day, still above level) / Failed."""
    if level and close < level:
        return "Failed"
    return "Slipped" if close < prev_close else "Held"


def follow_through(last: pd.Series, adx_min: float = 20) -> str:
    """stock-2 follow-through: trend (close > EMA50 ≥ EMA200) + momentum (MACD hist > 0, ADX ≥ min)."""
    trend = bool(last["Close"] > last["EMA50"] >= last["EMA200"])
    mom = bool(last["MACDHist"] > 0 and last["ADX"] >= adx_min)
    return "Strong" if trend and mom else ("Partial" if trend or mom else "Weak")


def scan_breakout(df: pd.DataFrame, cfg: dict) -> ScanResult:
    """Fresh close above the prior N-day high on expanding volume, not over-extended."""
    b = cfg["scanner"]["breakout"]
    last = df.iloc[-1]
    close, prior_high = float(last["Close"]), float(last["PriorHigh20"])
    ext = (close / prior_high - 1) * 100
    vol_ratio = float(last["VolumeRatio"]) if pd.notna(last["VolumeRatio"]) else 0.0
    range_pos = float(last["CloseRangePos"])
    trend = _trend_ok(last)

    ev = [
        Evidence("bo:close_above_20d_high", round(ext, 2), "> 0%", ext > 0),
        Evidence("bo:volume_ratio", round(vol_ratio, 2), f">= {b['volume_ratio_min']}", vol_ratio >= b["volume_ratio_min"]),
        Evidence("bo:extension_pct", round(ext, 2), f"<= {b['max_extension_pct']}%", ext <= b["max_extension_pct"]),
        Evidence("bo:close_in_upper_range", round(range_pos, 2), f">= {b['min_close_range_pos']}",
                 range_pos >= b["min_close_range_pos"]),
        Evidence("bo:trend_close>EMA50>EMA200", trend, True, trend),
    ]
    major = last.get("PriorCloseHigh60")
    clears_major = bool(pd.notna(major) and close > float(major))
    ev.append(Evidence("bo:clears_60d_closing_high", round(float(major), 2) if pd.notna(major) else None,
                       "close above", clears_major, "major level (stock-2)"))
    weights = [35, 25, 15, 10, 15, 0]
    score = sum(w for e, w in zip(ev, weights) if e.passed)
    # partial credit for volume
    if not ev[1].passed and ext > 0:
        score += 25 * max(0.0, min(1.0, (vol_ratio - 1) / (b["volume_ratio_min"] - 1 + 1e-9)))
    passed = all(e.passed for e in ev[:5])
    grade = grade_breakout(close, prior_high, float(last["RSI"]), vol_ratio, b.get("grades"))
    return ScanResult("breakout", passed, round(score, 1), ev,
                      {"breakout_level": round(prior_high, 2), "extension_pct": round(ext, 2),
                       "volume_ratio": round(vol_ratio, 2), "breakout_grade": grade,
                       "clears_60d_close_high": clears_major})


def scan_pre_breakout(df: pd.DataFrame, cfg: dict) -> ScanResult:
    """Tight base just below the 20-day high with volume drying up (VCP-style)."""
    p = cfg["scanner"]["pre_breakout"]
    last = df.iloc[-1]
    close = float(last["Close"])
    high20 = float(max(last["PriorHigh20"], last["High"]))
    low20 = float(min(last["PriorLow20"], last["Low"]))
    dist = (high20 / close - 1) * 100            # % below 20D high
    depth = (high20 - low20) / close * 100       # base depth
    vol_dry = float(last["AvgVolume5"]) < float(last["AvgVolume20"])
    trend = _trend_ok(last)
    below_high = close < float(last["PriorHigh20"])

    ev = [
        Evidence("pb:below_20d_high", below_high, True, below_high, "not yet broken out"),
        Evidence("pb:dist_to_20d_high_pct", round(dist, 2), f"<= {p['near_high_pct']}%", dist <= p["near_high_pct"]),
        Evidence("pb:base_depth_pct", round(depth, 2), f"<= {p['max_base_depth_pct']}%", depth <= p["max_base_depth_pct"]),
        Evidence("pb:volume_contraction_5d<20d", vol_dry, True, vol_dry),
        Evidence("pb:trend_close>EMA50>EMA200", trend, True, trend),
    ]
    weights = [0, 30, 25, 20, 25]
    score = sum(w for e, w in zip(ev, weights) if e.passed) if below_high else 0
    passed = below_high and ev[1].passed and ev[2].passed and trend
    return ScanResult("pre_breakout", passed, round(score, 1), ev,
                      {"pivot": round(high20, 2), "dist_to_pivot_pct": round(dist, 2), "base_depth_pct": round(depth, 2)})
