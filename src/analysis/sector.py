"""Sector strength: rank sectors by median return and breadth, relative to the benchmark."""
from __future__ import annotations

import numpy as np
import pandas as pd


def sector_strength(
    returns: pd.DataFrame, benchmark_return_pct: float
) -> pd.DataFrame:
    """
    returns: DataFrame with columns [symbol, sector, ret_20d_pct, ret_63d_pct, above_ema50]
    Returns one row per sector with a 0–100 `sector_score` (percentile rank).
    """
    if returns.empty:
        return pd.DataFrame(columns=["sector", "stocks", "median_ret_20d_pct", "median_ret_63d_pct",
                                     "breadth_pct", "excess_vs_benchmark_pct", "sector_score", "rank"])
    g = returns.groupby("sector")
    out = pd.DataFrame({
        "stocks": g.size(),
        "median_ret_20d_pct": g["ret_20d_pct"].median(),
        "median_ret_63d_pct": g["ret_63d_pct"].median(),
        "breadth_pct": g["above_ema50"].mean() * 100,
    }).reset_index()
    out["excess_vs_benchmark_pct"] = out["median_ret_63d_pct"] - benchmark_return_pct
    # composite: 3M momentum (50%), 1M momentum (30%), breadth (20%) — ranked
    composite = (
        out["median_ret_63d_pct"].rank(pct=True) * 0.5
        + out["median_ret_20d_pct"].rank(pct=True) * 0.3
        + out["breadth_pct"].rank(pct=True) * 0.2
    )
    out["sector_score"] = (composite.rank(pct=True) * 100).round(1) if len(out) > 1 else 50.0
    out = out.sort_values("sector_score", ascending=False).reset_index(drop=True)
    out["rank"] = np.arange(1, len(out) + 1)
    return out.round(2)
