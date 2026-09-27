"""⑤ Event agent: results / ex-dividend dates inside the holding window."""
from __future__ import annotations

import logging
from datetime import date
from typing import Callable

from src.data.corporate_actions import fetch_events
from src.data.nse import to_yahoo
from src.models import Candidate, Evidence

log = logging.getLogger(__name__)

FetchFn = Callable[[list[str]], dict[str, dict]]


class EventAgent:
    def __init__(self, settings: dict, rules: dict, fetch_fn: FetchFn | None = None):
        self.rules = rules
        suffix = settings["data"]["exchange_suffix"]
        workers = settings["data"]["max_workers"]
        self.fetch_fn = fetch_fn or (
            lambda syms: {s: v for s, v in zip(syms, fetch_events([to_yahoo(x, suffix) for x in syms], workers).values())}
        )

    def run(self, candidates: list[Candidate], as_of: date | None = None) -> None:
        if not candidates:
            return
        e = self.rules["events"]
        today = as_of or date.today()
        try:
            data = self.fetch_fn([c.symbol for c in candidates])
        except Exception as exc:
            log.error("Event calendar unavailable: %s", exc)
            data = {}

        for c in candidates:
            ev = data.get(c.symbol) or {}
            ne, xd = ev.get("next_earnings"), ev.get("ex_dividend")
            c.events = {"next_earnings": str(ne) if ne else None, "ex_dividend": str(xd) if xd else None}
            score = 100.0

            if not ev:
                score = 80.0
                c.risks.append("Event calendar unavailable — verify results date on NSE before entry.")
                c.evidence.append(Evidence("event:calendar", None, "available", False, "no data"))

            if ne:
                days = (ne - today).days
                in_blackout = 0 <= days <= e["earnings_blackout_days"]
                c.evidence.append(Evidence("event:days_to_results", days, f"> {e['earnings_blackout_days']}", not in_blackout,
                                           str(ne)))
                if in_blackout:
                    score = 0.0
                    c.risks.append(f"Results due {ne} ({days}d) — gap risk through the stop.")
                    if e["exclude_in_blackout"]:
                        c.rejected_reason = f"earnings blackout: results on {ne}"

            if xd:
                days = (xd - today).days
                if 0 <= days <= e["ex_dividend_warning_days"]:
                    score = min(score, 70.0)
                    c.risks.append(f"Ex-dividend {xd} — price adjusts down by the dividend; can touch a tight stop.")

            c.event_score = score
        log.info("Event check done for %d candidates", len(candidates))
