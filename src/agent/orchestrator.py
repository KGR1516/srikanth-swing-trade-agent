"""Pipeline orchestrator.

① SCHEDULE (GitHub Actions) → ② MARKET DATA → ③ DATA QUALITY → ④ FULL SCANNER
(technical · fundamental · sector) → ⑤ EVENT CHECK → ⑥ RISK ENGINE → ⑦ SETUP ENGINE
→ ⑧ FINAL REPORT (Excel · JSON · Markdown)

Every data provider can be injected, so the whole pipeline runs offline in tests.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from functools import partial
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import pandas as pd

from src.agent.event_agent import EventAgent
from src.agent.fundamental_agent import FundamentalAgent
from src.agent.market_agent import MarketAgent
from src.agent.report_agent import ReportAgent
from src.agent.risk_agent import RiskAgent
from src.agent.setup_agent import SetupAgent
from src.agent.technical_agent import TechnicalAgent
from src.analysis.scoring import gated_action, penalties_for, score_breakdown
from src.analysis.sector import sector_strength
from src.analysis.setup import live_check
from src.analysis.technical import enrich
from src.config import ROOT, Config, load_config
from src.data.market_data import load_prices, refresh_recent
from src.data.nse import fetch_bhavcopy, resolve_universe, static_universe
from src.data.quality import (clean_history, drop_holiday_rows, drop_incomplete_bar, project_partial_volume,
                              resolve_session, validate_history)
from src.models import Candidate, QualityReport, RunResult
from src.parallel import pmap
from src.scanner.liquidity import quick_liquid

log = logging.getLogger("swing_agent")


class DataUnavailableError(RuntimeError):
    """Raised when too little clean data is available to produce a trustworthy report."""


def run_scan(
    cfg: Config | None = None,
    prices: dict[str, pd.DataFrame] | None = None,
    benchmark: pd.DataFrame | None = None,
    fundamentals_fn: Callable | None = None,
    events_fn: Callable | None = None,
    as_of: date | None = None,
    output_dir: Path | None = None,
    write_reports: bool = True,
    now: datetime | None = None,
    session: str | None = None,
) -> RunResult:
    cfg = cfg or load_config()
    S, R = cfg.settings, cfg.rules
    tz = ZoneInfo(S["project"].get("timezone", "Asia/Kolkata"))
    now = now or datetime.now(tz)
    today = as_of or now.date()
    aft = S["project"].get("afternoon_from", "13:00").split(":")
    session = resolve_session(session, now, (int(aft[0]), int(aft[1])))
    log.info("Run session: %s (%s IST)", session, now.strftime("%H:%M"))

    # ── ② MARKET DATA ────────────────────────────────────────────
    sources: list[str] = []
    bhav = None
    n_cached = 0
    D, P = S["data"], S.get("performance") or {}
    suffix = D["exchange_suffix"]
    if prices is None:
        universe, universe_name = resolve_universe(cfg.universe, ROOT)
        log.info("② Market data: %d symbols (%s) + benchmark %s", len(universe), universe_name, D["benchmark"])
        prices, benchmark, n_cached = load_prices(
            list(universe), D["benchmark"], D["lookback_days"], cfg.path("raw"), suffix,
            D.get("download_batch_size", 50), D.get("download_retries", 2),
        )
        sources.append("Yahoo Finance (adjusted daily OHLCV, fundamentals, calendar)")
        if n_cached and session == "afternoon":
            # history was prefetched before the start time → only today's candle needs fetching now,
            # and only for stocks liquid enough to matter
            liquid_syms = [s for s, df in prices.items() if quick_liquid(df, S)]
            n = refresh_recent(prices, liquid_syms, D.get("refresh_recent_days", 5), suffix,
                               D.get("download_batch_size", 50))
            bench_box = {D["benchmark"]: benchmark}
            refresh_recent(bench_box, [D["benchmark"]], D.get("refresh_recent_days", 5), suffix)
            benchmark = bench_box[D["benchmark"]]
            sources.append(f"prefetched history + live refresh of {n} liquid stocks at {datetime.now(tz):%H:%M:%S} IST")
        if S["data"].get("nse_bhavcopy_crosscheck"):
            bhav = fetch_bhavcopy(today)
            if bhav is not None:
                sources.append(f"NSE bhavcopy {bhav.attrs.get('date')} (close cross-check)")
    else:  # tests / notebooks: data supplied directly, no universe download
        sources.append("injected data")
        static = static_universe(cfg.universe)
        universe = {s: static.get(s, "Unclassified") for s in prices}
        universe_name = "Injected data"

    partial_bars: dict[str, pd.Series] = {}
    if session == "afternoon":
        # provisional: keep today's forming candle, scale its volume up to a full-day estimate
        projected = 0
        for sym in list(prices):
            prices[sym], d = project_partial_volume(prices[sym], now)
            projected += d
        if projected:
            sources.append(f"PROVISIONAL — today's candle as of {now:%H:%M} IST, volume projected to full day "
                           f"({projected} symbols)")
    elif S["data_quality"].get("drop_incomplete_bar", True):
        dropped = 0
        for sym in list(prices):
            df0 = prices[sym]
            prices[sym], d = drop_incomplete_bar(df0, now)
            if d:
                partial_bars[sym] = df0.iloc[-1]
            dropped += d
        if benchmark is not None:
            benchmark, _ = drop_incomplete_bar(benchmark, now)
        if dropped:
            sources.append(f"setups from last completed session; today's live candle used only for status "
                           f"({dropped} symbols)")
            log.info("Market open: setups from last completed session (%d partial bars set aside)", dropped)

    # ── ③ DATA QUALITY ───────────────────────────────────────────
    quality: list[QualityReport] = []
    clean: dict[str, pd.DataFrame] = {}
    tol = S["data"].get("crosscheck_tolerance_pct", 1.5)
    for sym in universe:
        df = prices.get(sym)
        if df is None or df.empty:
            quality.append(QualityReport(sym, "FAIL", ["no_data_from_provider"]))
            continue
        df = clean_history(df)
        ref = None
        if bhav is not None and sym in bhav.index and \
                pd.Timestamp(df.index[-1]).date() == bhav.attrs.get("date"):
            ref = float(bhav.loc[sym, "Close"])
        q = validate_history(sym, df, S, today, ref, tol)
        quality.append(q)
        if q.status != "FAIL":
            clean[sym] = df
    log.info("③ Data quality: %d/%d passed", len(clean), len(universe))
    min_ratio = S["data_quality"].get("min_pass_ratio", 0.5)
    if not universe or len(clean) / len(universe) < min_ratio:
        raise DataUnavailableError(
            f"Only {len(clean)}/{len(universe)} symbols passed data quality (< {min_ratio:.0%}). "
            "Provider down or market holiday? Previous report left untouched."
        )

    if P.get("prefilter_liquidity", True):
        liquid = {s: df for s, df in clean.items() if quick_liquid(df, S)}
        skipped = len(clean) - len(liquid)
        if skipped:
            sources.append(f"{skipped} clearly illiquid stocks skipped before indicators")
            log.info("Liquidity pre-filter: %d of %d stocks go to the scanner", len(liquid), len(clean))
    else:
        liquid = clean
    syms = list(liquid)
    enriched = dict(zip(syms, pmap(partial(enrich, cfg=S), [liquid[s] for s in syms], P.get("workers", 0))))

    # ── ④ FULL SCANNER ───────────────────────────────────────────
    if benchmark is not None and not benchmark.empty:
        benchmark = drop_holiday_rows(benchmark.sort_index())
    regime = MarketAgent(R).run(benchmark, enriched)
    full_scan, cands = TechnicalAgent(S).run(enriched, benchmark, universe)

    # sector strength
    per = S["scanner"]["sector"]["period"]
    sec_rows = [{
        "symbol": s, "sector": universe.get(s, "Unclassified"),
        "ret_20d_pct": float(df["Return20"].iloc[-1] * 100),
        "ret_63d_pct": float(df["Close"].pct_change(per).iloc[-1] * 100),
        "above_ema50": bool(df["Close"].iloc[-1] > df["EMA50"].iloc[-1]),
    } for s, df in enriched.items()]
    bench_ret = 0.0
    if benchmark is not None and len(benchmark) > per:
        bench_ret = float(benchmark["Close"].iloc[-1] / benchmark["Close"].iloc[-per - 1] - 1) * 100
    sectors = sector_strength(pd.DataFrame(sec_rows), bench_ret)
    sec_score = dict(zip(sectors["sector"], sectors["sector_score"])) if not sectors.empty else {}

    sc = S["scoring"]
    min_rs = S["scanner"]["relative_strength"]["min_rs_percentile"]
    shortlist: list[Candidate] = sorted(
        (c for c in cands.values() if c.technical_score >= sc["min_technical_score"]),
        key=lambda c: (c.technical_score + c.rs_percentile) / 2, reverse=True,
    )[: sc["shortlist_size"]]
    for c in shortlist:
        c.sector_score = float(sec_score.get(c.sector, 50.0))
        if c.rs_percentile < min_rs:
            c.risks.append(f"Relative strength percentile {c.rs_percentile:.0f} < {min_rs} — lagging the market.")
    log.info("④ Scanner: %d setups, %d shortlisted", len(cands), len(shortlist))

    FundamentalAgent(S, R, cfg.path("raw"), fundamentals_fn).run(shortlist)
    for c in shortlist:  # sector may have been filled in from fundamentals
        c.sector_score = float(sec_score.get(c.sector, c.sector_score))
    if TechnicalAgent(S).add_confluence(shortlist, clean):
        sources.append("pandas-ta-classic indicator confluence")

    # ── ⑤ EVENT CHECK ────────────────────────────────────────────
    EventAgent(S, R, events_fn).run(shortlist, today)

    for c in shortlist:
        c.penalty = penalties_for(c.loss_making, c.failed_breakout, sc.get("penalties"))
        c.score_breakdown = score_breakdown(c.technical_score, c.rs_percentile, c.fundamental_score,
                                            c.sector_score, c.event_score, sc["weights"],
                                            c.confluence_score, c.penalty)
        c.final_score = c.score_breakdown["final"]
        if c.rs_percentile < min_rs and not c.rejected_reason:
            c.rejected_reason = f"relative strength {c.rs_percentile:.0f} < {min_rs}"
        ext_policy = S["scanner"]["breakout"].get("grades", {}).get("extended_policy", "watchlist")
        if c.breakout_grade == "Extended" and ext_policy == "watchlist" and not c.rejected_reason:
            c.rejected_reason = "breakout Extended (RSI ≥ 75 or > 5% above level) — wait for a pullback"

    # ── ⑥ RISK ENGINE ────────────────────────────────────────────
    envelopes = RiskAgent(S, R).run(shortlist, enriched, regime)

    # ── ⑦ SETUP ENGINE ───────────────────────────────────────────
    setups = SetupAgent(S, R).run(shortlist, enriched, envelopes)
    chosen = {s.symbol for s in setups}
    if session == "morning":
        if n_cached and setups:
            # history came from the pre-start prefetch → fetch a fresh live candle for the setups only
            fresh: dict[str, pd.DataFrame] = {}
            refresh_recent(fresh, [s_.symbol for s_ in setups], D.get("refresh_recent_days", 5), suffix)
            for sym, df in fresh.items():
                if len(df) and pd.Timestamp(df.index[-1]).date() == now.date():
                    partial_bars[sym] = df.iloc[-1]
        for s_ in setups:
            s_.today_status, s_.today_last = live_check(s_, partial_bars.get(s_.symbol))
    bands = sc.get("action_bands")
    for s_ in setups:
        s_.action = gated_action(s_.final_score, True, bands)
    for c in shortlist:
        c.action = "BUY" if c.symbol in chosen else gated_action(c.final_score, False, bands)
    watchlist = sorted((c for c in shortlist if c.symbol not in chosen),
                       key=lambda c: c.final_score, reverse=True)[: S["report"]["watchlist_n"]]

    # enrich full scan with downstream scores / status
    if not full_scan.empty:
        info = {c.symbol: c for c in shortlist}
        full_scan["sector_score"] = full_scan["sector"].map(sec_score).fillna(50.0)
        full_scan["fundamental_score"] = full_scan["symbol"].map(lambda s: info[s].fundamental_score if s in info else None)
        full_scan["event_score"] = full_scan["symbol"].map(lambda s: info[s].event_score if s in info else None)
        full_scan["confluence_score"] = full_scan["symbol"].map(lambda s: info[s].confluence_score if s in info else None)
        full_scan["penalty"] = full_scan["symbol"].map(lambda s: info[s].penalty if s in info else None)
        full_scan["final_score"] = full_scan["symbol"].map(lambda s: info[s].final_score if s in info else None)
        setup_action = {s_.symbol: s_.action for s_ in setups}
        full_scan["action"] = [
            setup_action.get(s) or (info[s].action if s in info else "SKIP") for s in full_scan["symbol"]
        ]
        full_scan["status"] = [
            "SETUP" if s in chosen else (info[s].rejected_reason or "watchlist") if s in info else st
            for s, st in zip(full_scan["symbol"], full_scan["status"])
        ]

    as_of_str = regime.as_of if regime.as_of != "n/a" else max(
        (q.last_date for q in quality if q.last_date), default=str(today))
    result = RunResult(
        as_of=as_of_str,
        generated_at=datetime.now(tz).strftime("%Y-%m-%d %H:%M %Z"),
        capital=float(S["capital"]["total"]),
        universe_size=len(universe),
        universe_name=universe_name,
        regime=regime,
        setups=setups,
        watchlist=watchlist,
        full_scan=full_scan,
        sectors=sectors,
        quality=quality,
        data_sources=sources,
        session=session,
        session_label={
            "morning": f"Morning run {now:%H:%M} IST — setups from the {{as_of}} close, with live status today",
            "afternoon": f"Afternoon run {now:%H:%M} IST — PROVISIONAL: today's candle is not final until 15:30",
            "eod": "Post-close run — final, on completed daily candles",
        }[session],
    )
    result.session_label = result.session_label.replace("{as_of}", result.as_of)

    # ── ⑧ FINAL REPORT ───────────────────────────────────────────
    if write_reports:
        ReportAgent(S, output_dir or cfg.path("output")).run(result)
    log.info("⑧ Done: %d setups, %d on watchlist (regime %s)", len(setups), len(watchlist), regime.regime)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run_scan()
