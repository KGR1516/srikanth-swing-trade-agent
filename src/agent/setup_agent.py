"""⑦ Setup agent: build next-session setups, then apply portfolio limits."""
from __future__ import annotations

import logging

import pandas as pd

from src.analysis.risk import RiskEnvelope, apply_portfolio_limits
from src.analysis.setup import build_setup
from src.models import Candidate, Setup

log = logging.getLogger(__name__)


class SetupAgent:
    def __init__(self, settings: dict, rules: dict):
        self.settings, self.rules = settings, rules

    def run(
        self,
        candidates: list[Candidate],
        enriched: dict[str, pd.DataFrame],
        envelopes: dict[str, RiskEnvelope],
    ) -> list[Setup]:
        min_score = self.settings["scoring"]["min_final_score"]
        built: list[Setup] = []
        by_symbol = {c.symbol: c for c in candidates}

        for c in candidates:
            if c.rejected_reason:
                continue
            if c.final_score < min_score:
                c.rejected_reason = f"final score {c.final_score:.1f} < {min_score}"
                continue
            setup, why = build_setup(c, enriched[c.symbol], envelopes[c.symbol], self.rules, self.settings)
            if setup is None:
                c.rejected_reason = f"setup: {why}"
                continue
            built.append(setup)

        kept, dropped = apply_portfolio_limits(built, self.rules, self.settings["report"]["top_n"])
        for s, why in dropped:
            by_symbol[s.symbol].rejected_reason = f"portfolio: {why}"
        for i, s in enumerate(kept, 1):
            s.rank = i
        log.info("Setup engine: %d built, %d kept after portfolio limits", len(built), len(kept))
        return kept
