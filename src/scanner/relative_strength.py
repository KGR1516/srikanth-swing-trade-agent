"""Relative strength vs the benchmark, ranked across the universe."""
from __future__ import annotations

import pandas as pd


def relative_strength(close: pd.Series, bench_close: pd.Series, period: int = 63) -> dict:
    """Excess return vs benchmark and whether the RS line is at a new period high."""
    aligned = pd.concat([close.rename("s"), bench_close.rename("b")], axis=1, join="inner").dropna()
    if len(aligned) <= period:
        return {"rs_excess_pct": float("nan"), "rs_line_new_high": False, "stock_ret_pct": float("nan")}
    s_ret = aligned["s"].iloc[-1] / aligned["s"].iloc[-period - 1] - 1
    b_ret = aligned["b"].iloc[-1] / aligned["b"].iloc[-period - 1] - 1
    rs_line = aligned["s"] / aligned["b"]
    return {
        "rs_excess_pct": round((s_ret - b_ret) * 100, 2),
        "stock_ret_pct": round(s_ret * 100, 2),
        "rs_line_new_high": bool(rs_line.iloc[-1] >= rs_line.tail(period).max() * 0.995),
    }


def rank_relative_strength(excess: dict[str, float]) -> dict[str, float]:
    """Percentile rank 0–100 (higher = stronger). NaN → 0."""
    s = pd.Series(excess, dtype=float)
    valid = s.dropna()
    if valid.empty:
        return {k: 0.0 for k in excess}
    ranks = (valid.rank(pct=True) * 100).round(1)
    return {k: float(ranks.get(k, 0.0)) for k in excess}
