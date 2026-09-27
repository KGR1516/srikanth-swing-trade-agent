from src.analysis.fundamental import score_fundamentals
from src.analysis.technical import enrich
from src.scanner.breakout import scan_breakout, scan_pre_breakout
from src.scanner.liquidity import scan_liquidity
from src.scanner.momentum import scan_momentum
from src.scanner.relative_strength import rank_relative_strength, relative_strength
from tests.conftest import make_ohlcv


def test_breakout_detected(breakout_df, cfg):
    res = scan_breakout(enrich(breakout_df, cfg.settings), cfg.settings)
    assert res.passed, [e for e in res.evidence if not e.passed]
    assert res.score >= 90


def test_no_breakout_on_ordinary_day(uptrend, cfg):
    df = uptrend.copy()
    df.iloc[-1, df.columns.get_loc("Close")] = df["Low"].iloc[-21:-1].min()
    df.iloc[-1, df.columns.get_loc("Low")] = df["Close"].iloc[-1] * 0.99
    assert not scan_breakout(enrich(df, cfg.settings), cfg.settings).passed


def test_breakout_is_not_also_pre_breakout(breakout_df, cfg):
    assert not scan_pre_breakout(enrich(breakout_df, cfg.settings), cfg.settings).passed


def test_liquidity_filter(cfg):
    thin = make_ohlcv(volume=20_000)
    assert not scan_liquidity(enrich(thin, cfg.settings), cfg.settings).passed


def test_momentum_uptrend_vs_downtrend(cfg):
    up = scan_momentum(enrich(make_ohlcv(seed=3, drift=0.002), cfg.settings), cfg.settings)
    down = scan_momentum(enrich(make_ohlcv(seed=3, drift=-0.002), cfg.settings), cfg.settings)
    assert up.score > down.score and not down.passed


def test_relative_strength_rank():
    bench = make_ohlcv(seed=9, drift=0.0005)["Close"]
    strong = relative_strength(make_ohlcv(seed=9, drift=0.003)["Close"], bench)
    weak = relative_strength(make_ohlcv(seed=9, drift=-0.001)["Close"], bench)
    assert strong["rs_excess_pct"] > 0 > weak["rs_excess_pct"]
    ranks = rank_relative_strength({"S": strong["rs_excess_pct"], "W": weak["rs_excess_pct"]})
    assert ranks["S"] > ranks["W"]


def test_fundamentals_units_and_missing(cfg):
    raw = {"roe": 0.18, "debt_to_equity_pct": 45.0, "revenue_growth": 0.12, "earnings_growth": 0.2,
           "profit_margin": 0.1, "pe": 30, "market_cap": 5e11}
    score, ev, snap = score_fundamentals(raw, cfg.rules)
    assert snap["debt_to_equity"] == 0.45           # Yahoo % → ratio
    assert score == 100
    neutral, _, _ = score_fundamentals({}, cfg.rules)
    assert neutral == cfg.rules["fundamentals"]["missing_data_score"]
