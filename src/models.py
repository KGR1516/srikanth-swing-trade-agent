"""Shared data models passed between pipeline stages."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Evidence:
    """One rule check — keeps every decision auditable."""
    rule: str
    value: Any
    threshold: Any
    passed: bool
    note: str = ""


@dataclass
class ScanResult:
    name: str
    passed: bool
    score: float                      # 0–100
    evidence: list[Evidence] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class QualityReport:
    symbol: str
    status: str                       # PASS / WARN / FAIL
    issues: list[str] = field(default_factory=list)
    last_date: str | None = None
    bars: int = 0


@dataclass
class MarketRegime:
    as_of: str
    regime: str                       # bullish / neutral / bearish
    exposure: float                   # multiplier on risk per trade
    benchmark_close: float
    benchmark_return_20d_pct: float
    above_ema50: bool
    above_ema200: bool
    breadth_pct: float                # % of universe above 50-EMA
    notes: list[str] = field(default_factory=list)


@dataclass
class Candidate:
    """A stock moving through stages ④–⑦."""
    symbol: str
    sector: str
    as_of: str
    close: float
    setup_type: str = ""              # Breakout / Pre-Breakout / Pullback
    technical_score: float = 0.0
    rs_percentile: float = 0.0
    rs_excess_pct: float = 0.0
    sector_score: float = 50.0
    fundamental_score: float = 50.0
    event_score: float = 100.0
    confluence_score: float | None = None        # 0–100, None = unavailable
    penalty: float = 0.0                         # stock-2 penalties (loss-making, failed breakout)
    final_score: float = 0.0
    action: str = ""                             # BUY NOW / BUY / WATCH / AVOID / SKIP
    breakout_grade: str = ""                     # Strong Fresh / Fresh / Solid / Extended / Failed
    live_status: str = ""                        # Held / Slipped / Failed
    follow_through: str = ""                     # Strong / Partial / Weak
    loss_making: bool = False
    failed_breakout: bool = False
    confluence: dict[str, Any] = field(default_factory=dict)
    score_breakdown: dict[str, Any] = field(default_factory=dict)
    fundamentals: dict[str, Any] = field(default_factory=dict)
    events: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    rejected_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Setup:
    """⑦ Next-session trade setup — research output, not an order."""
    rank: int
    symbol: str
    sector: str
    as_of: str
    setup_type: str
    close: float
    entry_trigger: float              # buy-stop above this price
    entry_limit: float                # don't chase beyond (trigger + max gap)
    stop: float
    target1: float
    target2: float
    risk_per_share: float
    stop_pct: float
    rr_t1: float
    rr_t2: float
    quantity: int
    position_value: float
    capital_at_risk: float
    room_to_52w_high_r: float | None
    valid_sessions: int
    time_stop_sessions: int
    technical_score: float
    rs_percentile: float
    sector_score: float
    fundamental_score: float
    event_score: float
    final_score: float
    action: str = "BUY"
    confluence_score: float | None = None
    penalty: float = 0.0
    breakout_grade: str = ""
    live_status: str = ""
    follow_through: str = ""
    score_breakdown: dict[str, Any] = field(default_factory=dict)
    confluence: dict[str, Any] = field(default_factory=dict)
    today_status: str = ""                       # morning run: live status of the setup today
    today_last: float | None = None
    status: str = "RESEARCH SETUP"
    evidence: list[Evidence] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunResult:
    """Everything the report writers need from one pipeline run."""
    as_of: str
    generated_at: str
    capital: float
    universe_size: int
    regime: MarketRegime
    setups: list[Setup]
    watchlist: list[Candidate]
    full_scan: Any                    # pandas DataFrame
    sectors: Any                      # pandas DataFrame
    quality: list[QualityReport]
    data_sources: list[str] = field(default_factory=list)
    session: str = "eod"                          # morning | afternoon | eod
    session_label: str = ""
    disclaimer: str = (
        "Research output only — not investment advice and not an order. "
        "Verify prices, results dates and liquidity before trading; size positions to your own risk tolerance."
    )
