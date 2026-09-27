"""Fundamental quality score (0–100) from a provider snapshot.

Yahoo units handled here:
  returnOnEquity, revenueGrowth, earningsGrowth, profitMargins → fractions (0.15 = 15%)
  debtToEquity → percent (45.0 = 0.45×)
  marketCap → INR
"""
from __future__ import annotations

from typing import Any

from src.models import Evidence


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None  # drop NaN


def normalise_snapshot(raw: dict) -> dict[str, float | None]:
    roe = _f(raw.get("roe"))
    de = _f(raw.get("debt_to_equity_pct"))
    rg = _f(raw.get("revenue_growth"))
    eg = _f(raw.get("earnings_growth"))
    if eg is None:
        eg = _f(raw.get("earnings_quarterly_growth"))
    pm = _f(raw.get("profit_margin"))
    mc = _f(raw.get("market_cap"))
    eps = _f(raw.get("trailing_eps"))
    return {
        "roe_pct": None if roe is None else roe * 100,
        "debt_to_equity": None if de is None else de / 100,
        "revenue_growth_pct": None if rg is None else rg * 100,
        "eps_growth_pct": None if eg is None else eg * 100,
        "profit_margin_pct": None if pm is None else pm * 100,
        "pe": _f(raw.get("pe")),
        "market_cap_cr": None if mc is None else mc / 1e7,
        "trailing_eps": eps,
        # stock-2 penalty trigger: negative margin or negative trailing EPS
        "loss_making": bool((pm is not None and pm < 0) or (eps is not None and eps < 0)),
    }


def score_fundamentals(raw: dict, rules: dict) -> tuple[float, list[Evidence], dict]:
    """Return (score, evidence, normalised snapshot)."""
    f = rules["fundamentals"]
    th, wt = f["thresholds"], f["weights"]
    snap = normalise_snapshot(raw or {})

    checks = [
        ("roe", "ROE %", snap["roe_pct"], th["min_roe_pct"], lambda v, t: v >= t, ">="),
        ("debt_to_equity", "Debt/Equity", snap["debt_to_equity"], th["max_debt_to_equity"], lambda v, t: v <= t, "<="),
        ("revenue_growth", "Revenue growth %", snap["revenue_growth_pct"], th["min_revenue_growth_pct"], lambda v, t: v >= t, ">="),
        ("eps_growth", "EPS growth %", snap["eps_growth_pct"], th["min_eps_growth_pct"], lambda v, t: v >= t, ">="),
        ("profit_margin", "Profit margin %", snap["profit_margin_pct"], th["min_profit_margin_pct"], lambda v, t: v >= t, ">="),
        ("valuation", "P/E", snap["pe"], th["max_pe"], lambda v, t: 0 < v <= t, "<="),
    ]
    evidence: list[Evidence] = []
    got = total = 0.0
    all_weight = sum(wt.get(k, 0) for k, *_ in checks)
    for key, label, value, threshold, fn, op in checks:
        w = wt.get(key, 0)
        if value is None:
            evidence.append(Evidence(f"fund:{label}", None, f"{op} {threshold}", False, "unavailable"))
            continue
        ok = bool(fn(value, threshold))
        total += w
        got += w if ok else 0
        evidence.append(Evidence(f"fund:{label}", round(value, 2), f"{op} {threshold}", ok))

    coverage = total / all_weight * 100 if all_weight else 0
    if coverage < f["min_coverage_pct"]:
        score = float(f["missing_data_score"])
        evidence.append(Evidence("fund:coverage", round(coverage, 1), f">= {f['min_coverage_pct']}%", False,
                                 "insufficient data → neutral score"))
    else:
        score = got / total * 100

    mc = snap["market_cap_cr"]
    if mc is not None and mc < th["min_market_cap_cr"]:
        score = min(score, 40.0)
        evidence.append(Evidence("fund:Market cap ₹cr", round(mc), f">= {th['min_market_cap_cr']}", False,
                                 "small cap — score capped at 40"))
    return round(score, 1), evidence, snap
