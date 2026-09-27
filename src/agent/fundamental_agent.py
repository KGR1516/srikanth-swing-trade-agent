"""Fundamental agent: quality score for the technical shortlist only (saves API calls)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from src.analysis.fundamental import score_fundamentals
from src.data.fundamentals import fetch_fundamentals
from src.data.nse import to_yahoo
from src.models import Candidate

log = logging.getLogger(__name__)

FetchFn = Callable[[list[str]], dict[str, dict]]


class FundamentalAgent:
    def __init__(self, settings: dict, rules: dict, cache_dir: Path | None = None, fetch_fn: FetchFn | None = None):
        self.settings, self.rules = settings, rules
        suffix = settings["data"]["exchange_suffix"]
        workers = settings["data"]["max_workers"]
        self.fetch_fn = fetch_fn or (
            lambda syms: {s: v for s, v in zip(syms, fetch_fundamentals(
                [to_yahoo(x, suffix) for x in syms], cache_dir, workers).values())}
        )

    def run(self, candidates: list[Candidate]) -> None:
        if not candidates:
            return
        try:
            data = self.fetch_fn([c.symbol for c in candidates])
        except Exception as exc:  # provider down → neutral, never a signal
            log.error("Fundamentals unavailable: %s", exc)
            data = {}
        for c in candidates:
            score, evidence, snap = score_fundamentals(data.get(c.symbol, {}), self.rules)
            c.fundamental_score = score
            c.fundamentals = snap
            c.evidence.extend(evidence)
            if score < 40:
                c.risks.append(f"Weak fundamentals (score {score:.0f}) — treat as a pure trading setup.")
            if snap.get("loss_making"):
                c.loss_making = True
                c.risks.append("Loss-making company (negative margin/EPS) — score penalised.")
            pe, caution = snap.get("pe"), self.rules["fundamentals"].get("pe_caution")
            if pe and caution and pe > caution:
                c.risks.append(f"Rich valuation: P/E {pe:.0f} > {caution} — less margin for error on bad news.")
            raw = data.get(c.symbol) or {}
            if c.sector == "Unclassified" and raw.get("yf_sector"):
                c.sector = str(raw["yf_sector"])
        log.info("Fundamentals scored for %d candidates", len(candidates))
