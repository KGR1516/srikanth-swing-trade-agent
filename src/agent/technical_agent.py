"""Technical agent: liquidity → setup scanners → momentum → relative strength (+ confluence)."""
from __future__ import annotations

import logging

import pandas as pd

from src.analysis import confluence as conf_engine
from src.analysis.technical import recent_failed_breakout
from src.models import Candidate
from src.scanner.breakout import follow_through, live_status, scan_breakout, scan_pre_breakout
from src.scanner.liquidity import scan_liquidity
from src.scanner.momentum import scan_momentum, scan_pullback
from src.scanner.relative_strength import rank_relative_strength, relative_strength

log = logging.getLogger(__name__)

SETUP_PRIORITY = [("Breakout", "breakout"), ("Pre-Breakout", "pre_breakout"), ("Pullback", "pullback")]


def _num(v, nd=2):
    return round(float(v), nd) if pd.notna(v) else None


class TechnicalAgent:
    def __init__(self, settings: dict):
        self.cfg = settings

    def run(
        self,
        enriched: dict[str, pd.DataFrame],
        benchmark: pd.DataFrame,
        sectors: dict[str, str],
    ) -> tuple[pd.DataFrame, dict[str, Candidate]]:
        rows: list[dict] = []
        cands: dict[str, Candidate] = {}
        rs_long: dict[str, float] = {}
        rs_short: dict[str, float] = {}
        rsc = self.cfg["scanner"]["relative_strength"]
        bo_cfg = self.cfg["scanner"]["breakout"]
        adx_min = self.cfg["scanner"]["momentum"]["adx_min"]
        bench_close = benchmark["Close"] if benchmark is not None and not benchmark.empty else None

        for sym, df in enriched.items():
            last, prev = df.iloc[-1], df.iloc[-2]
            liq = scan_liquidity(df, self.cfg)
            ft = follow_through(last, adx_min)
            failed = recent_failed_breakout(df, bo_cfg.get("failed_lookback", 5))
            row = {
                "symbol": sym, "sector": sectors.get(sym, "Unclassified"),
                "as_of": str(pd.Timestamp(df.index[-1]).date()), "close": round(float(last["Close"]), 2),
                "avg_traded_value_cr": liq.metrics["avg_traded_value_cr"], "liquid": liq.passed,
                "rsi": _num(last["RSI"], 1), "adx": _num(last["ADX"], 1), "atr_pct": _num(last["ATRPct"]),
                "volume_ratio": _num(last["VolumeRatio"]), "ext_ema20_pct": _num(last["ExtensionEMA20Pct"]),
                # stock-2 informational columns
                "supertrend": "Up" if last.get("SupertrendDir", 0) > 0 else "Down",
                "bb_percent": _num(last.get("BBPercent"), 3), "stoch_k": _num(last.get("StochK"), 1),
                "vwap20_dist_pct": _num(last.get("VWAPDistPct")), "obv_rising": bool(last.get("OBVRising", False)),
                "follow_through": ft, "failed_breakout": failed,
            }
            per_s = rsc.get("short_period", 20)
            if bench_close is not None:
                rs = relative_strength(df["Close"], bench_close, rsc["period"])
                rs_s = relative_strength(df["Close"], bench_close, per_s)
            else:
                rs = {"rs_excess_pct": _num(df["Close"].pct_change(rsc["period"]).iloc[-1] * 100),
                      "rs_line_new_high": False}
                rs_s = {"rs_excess_pct": _num(df["Close"].pct_change(per_s).iloc[-1] * 100)}
            row.update({"rs_excess_pct": rs["rs_excess_pct"], "rs_excess_20d_pct": rs_s["rs_excess_pct"],
                        "rs_line_new_high": rs["rs_line_new_high"]})
            rs_long[sym], rs_short[sym] = rs["rs_excess_pct"], rs_s["rs_excess_pct"]

            if not liq.passed:
                row.update(setup_type="", breakout_grade="", technical_score=0.0, status="illiquid")
                rows.append(row)
                continue

            scans = {
                "breakout": scan_breakout(df, self.cfg),
                "pre_breakout": scan_pre_breakout(df, self.cfg),
                "pullback": scan_pullback(df, self.cfg),
            }
            mom = scan_momentum(df, self.cfg)
            setup_type, setup_key = next(((n, k) for n, k in SETUP_PRIORITY if scans[k].passed), ("", ""))
            setup_score = scans[setup_key].score if setup_key else max(s.score for s in scans.values())
            tech = 0.5 * setup_score + 0.5 * mom.score
            if not setup_key:
                tech *= 0.6  # no actionable pattern → discounted

            grade, status = "", ""
            if setup_key == "breakout":
                grade = scans["breakout"].metrics["breakout_grade"]
                status = live_status(float(last["Close"]), float(prev["Close"]), float(last["PriorHigh20"]))
                tech += bo_cfg.get("grades", {}).get("grade_bonus", {}).get(grade, 0)
            elif failed:
                grade = "Prior BO failed"
            tech = round(min(100.0, tech), 1)

            row.update({
                "setup_type": setup_type, "breakout_grade": grade, "live_status": status,
                "technical_score": tech, "momentum_score": mom.score,
                "breakout_score": scans["breakout"].score, "pre_breakout_score": scans["pre_breakout"].score,
                "pullback_score": scans["pullback"].score, "pct_of_52w_high": mom.metrics["pct_of_52w_high"],
                "status": "setup" if setup_key else "no pattern",
            })
            rows.append(row)

            if setup_key:
                evidence = liq.evidence + scans[setup_key].evidence + mom.evidence
                risks = []
                if float(last["RSI"]) > 70:
                    risks.append(f"RSI {float(last['RSI']):.0f} — short-term overbought.")
                if setup_type == "Breakout":
                    if scans["breakout"].metrics["volume_ratio"] < 2:
                        risks.append("Breakout volume is adequate but not exceptional (< 2× average).")
                    if grade == "Extended":
                        risks.append("Breakout graded Extended (RSI ≥ 75 or > 5% above level) — wait for a pullback.")
                    if not scans["breakout"].metrics.get("clears_60d_close_high"):
                        risks.append("Still below the 60-day closing high — major resistance overhead.")
                if failed:
                    risks.append("A breakout in the last 5 sessions failed (closed back below its level).")
                if ft == "Weak":
                    risks.append("Weak follow-through: neither trend nor momentum confirms.")
                if float(last["ATRPct"]) > 4:
                    risks.append(f"High volatility (ATR {float(last['ATRPct']):.1f}% of price).")
                cands[sym] = Candidate(
                    symbol=sym, sector=row["sector"], as_of=row["as_of"], close=row["close"],
                    setup_type=setup_type, technical_score=tech, rs_excess_pct=rs["rs_excess_pct"] or 0.0,
                    breakout_grade=grade, live_status=status, follow_through=ft, failed_breakout=failed,
                    metrics={**scans[setup_key].metrics, **mom.metrics, "rs_line_new_high": rs["rs_line_new_high"],
                             "rs_excess_20d_pct": rs_s["rs_excess_pct"]},
                    evidence=evidence, risks=risks,
                )

        # blended RS percentile: 70% 3-month rank + 30% 1-month rank (stock-2 used 20 days)
        blend = rsc.get("blend", {"long": 1.0, "short": 0.0})
        r_long, r_short = rank_relative_strength(rs_long), rank_relative_strength(rs_short)
        wl, ws = float(blend.get("long", 1.0)), float(blend.get("short", 0.0))
        ranks = {s: round((r_long.get(s, 0) * wl + r_short.get(s, 0) * ws) / ((wl + ws) or 1), 1) for s in rs_long}
        for row in rows:
            row["rs_percentile"] = ranks.get(row["symbol"], 0.0)
        for sym, c in cands.items():
            c.rs_percentile = ranks.get(sym, 0.0)

        scan = pd.DataFrame(rows)
        if not scan.empty:
            scan = scan.sort_values(["technical_score", "rs_percentile"], ascending=False).reset_index(drop=True)
        log.info("Technical scan: %d stocks, %d liquid, %d with setups",
                 len(scan), int(scan["liquid"].sum()) if not scan.empty else 0, len(cands))
        return scan, cands

    def add_confluence(self, candidates: list[Candidate], frames: dict[str, pd.DataFrame]) -> bool:
        """Full-catalogue indicator consensus for the shortlist only (≈0.8 s/stock)."""
        c_cfg = self.cfg.get("confluence") or {}
        if not c_cfg.get("enabled", True) or not candidates:
            return False
        if not conf_engine.available():
            log.warning("confluence enabled but pandas-ta-classic is not installed — weight dropped")
            return False
        for c in candidates:
            try:
                res = conf_engine.confluence_signals(frames[c.symbol], c_cfg.get("min_votes", 3))
            except Exception as exc:  # never let one indicator break a symbol
                log.debug("confluence failed for %s: %s", c.symbol, exc)
                continue
            c.confluence = res
            c.confluence_score = res.get("confluence_score")
            if c.confluence_score is not None and c.confluence_score < 40:
                c.risks.append(f"Indicator confluence only {c.confluence_score:.0f}% bullish — most indicators disagree.")
        log.info("Confluence computed for %d candidates", len(candidates))
        return True
