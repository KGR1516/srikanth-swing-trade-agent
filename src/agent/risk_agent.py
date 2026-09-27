"""⑥ Risk agent: regime-adjusted risk budget and pre-trade volatility checks."""
from __future__ import annotations

import logging

import pandas as pd

from src.analysis.risk import RiskEnvelope, risk_envelope
from src.models import Candidate, MarketRegime

log = logging.getLogger(__name__)


class RiskAgent:
    def __init__(self, settings: dict, rules: dict):
        self.capital = float(settings["capital"]["total"])
        self.rules = rules

    def run(
        self, candidates: list[Candidate], enriched: dict[str, pd.DataFrame], regime: MarketRegime
    ) -> dict[str, RiskEnvelope]:
        envelopes: dict[str, RiskEnvelope] = {}
        for c in candidates:
            env = risk_envelope(enriched[c.symbol], self.capital, regime.exposure, self.rules)
            envelopes[c.symbol] = env
            if not env.approved and not c.rejected_reason:
                c.rejected_reason = f"risk: {env.reason}"
        approved = sum(e.approved for e in envelopes.values())
        log.info("Risk engine: %d/%d candidates within risk limits (exposure %.2f)",
                 approved, len(candidates), regime.exposure)
        return envelopes
