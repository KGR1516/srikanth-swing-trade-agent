"""Weighted composite score (0–100), stock-2 penalties and action verdicts."""
from __future__ import annotations

DEFAULT_WEIGHTS = {"technical": 0.34, "relative_strength": 0.17, "fundamental": 0.17,
                   "sector": 0.085, "event": 0.085, "confluence": 0.15}

DEFAULT_BANDS = [
    {"min": 80, "action": "BUY NOW"},
    {"min": 65, "action": "BUY"},
    {"min": 50, "action": "WATCH"},
    {"min": 35, "action": "AVOID"},
    {"min": 0, "action": "SKIP"},
]
ACTION_ORDER = ["SKIP", "AVOID", "WATCH", "BUY", "BUY NOW"]


def score_breakdown(
    technical: float,
    relative_strength: float,
    fundamental: float,
    sector: float,
    event: float,
    weights: dict | None = None,
    confluence: float | None = None,
    penalty: float = 0.0,
) -> dict:
    """Per-component points (they sum to the weighted score) + penalty + final.

    A missing confluence reading drops its weight and the rest renormalise, so a stock is
    never marked down for a library/data gap it isn't responsible for.
    """
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    parts = {"technical": technical, "relative_strength": relative_strength,
             "fundamental": fundamental, "sector": sector, "event": event}
    if confluence is not None:
        parts["confluence"] = confluence
    denom = sum(w.get(k, 0) for k in parts) or 1.0
    pts = {f"{k}_pts": round(float(v) * w.get(k, 0) / denom, 1) for k, v in parts.items()}
    weighted = sum(float(v) * w.get(k, 0) for k, v in parts.items()) / denom
    final = max(0.0, min(100.0, weighted + penalty))
    return {**pts, "weighted": round(weighted, 1), "penalty": penalty, "final": round(final, 1)}


def total_score(
    technical: float,
    relative_strength: float,
    fundamental: float,
    sector: float,
    event: float,
    weights: dict | None = None,
    confluence: float | None = None,
    penalty: float = 0.0,
) -> float:
    return score_breakdown(technical, relative_strength, fundamental, sector, event,
                           weights, confluence, penalty)["final"]


def penalties_for(loss_making: bool, failed_breakout: bool, cfg: dict | None = None) -> float:
    p = {"loss_making": -8, "failed_breakout": -15, **(cfg or {})}
    return float((p["loss_making"] if loss_making else 0) + (p["failed_breakout"] if failed_breakout else 0))


def action_for(score: float, bands: list | None = None) -> str:
    for band in sorted(bands or DEFAULT_BANDS, key=lambda b: b["min"], reverse=True):
        if score >= band["min"]:
            return band["action"]
    return "SKIP"


def gated_action(score: float, is_setup: bool, bands: list | None = None) -> str:
    """Verdict that respects the pipeline's gates.

    A stock that passed every gate (risk, events, portfolio) is at least BUY;
    one that was rejected can never read BUY / BUY NOW, however high its score.
    """
    a = action_for(score, bands)
    idx = ACTION_ORDER.index(a) if a in ACTION_ORDER else 0
    if is_setup:
        return ACTION_ORDER[max(idx, ACTION_ORDER.index("BUY"))]
    return ACTION_ORDER[min(idx, ACTION_ORDER.index("WATCH"))]
