"""Human-readable Markdown report (also used for Telegram / GitHub summary)."""
from __future__ import annotations

from pathlib import Path

from src.models import RunResult

REGIME_ICON = {"bullish": "🟢", "neutral": "🟡", "bearish": "🔴"}


def _inr(x: float) -> str:
    return f"₹{x:,.0f}"


def render_markdown(res: RunResult) -> str:
    r = res.regime
    L: list[str] = [
        f"# Swing Trade Agent — setups for the session after {res.as_of}",
        "",
        f"_Generated {res.generated_at} · Universe: {res.universe_name or 'custom'} ({res.universe_size} stocks) · "
        f"Capital {_inr(res.capital)}_",
        "",
        f"**{res.session_label}**" if res.session_label else "",
        "",
        f"## Market regime: {REGIME_ICON.get(r.regime, '')} {r.regime.upper()} (risk × {r.exposure:g})",
        "",
        f"- Nifty 50: {r.benchmark_close:,.2f} ({r.benchmark_return_20d_pct:+.2f}% 20D)",
    ]
    L += [f"- {n}" for n in r.notes]
    fs = res.full_scan
    if fs is not None and not fs.empty and "action" in fs:
        vc = fs["action"].value_counts()
        L.append("- Verdicts: " + " · ".join(f"{a} {int(vc.get(a, 0))}" for a in
                                             ["BUY NOW", "BUY", "WATCH", "AVOID", "SKIP"]))
    L += ["", f"## Next-session setups ({len(res.setups)})", ""]

    if res.setups:
        L += [
            "| # | Symbol | Action | Setup | Grade | Close | Entry ≥ | Don't chase > | Stop | Stop % | T1 (R) | T2 (R) | Qty | At risk | Score |",
            "|---:|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for s in res.setups:
            L.append(
                f"| {s.rank} | **{s.symbol}** | {s.action} | {s.setup_type} | {s.breakout_grade or '—'} | "
                f"{s.close:,.2f} | {s.entry_trigger:,.2f} | "
                f"{s.entry_limit:,.2f} | {s.stop:,.2f} | {s.stop_pct:.1f}% | {s.target1:,.2f} ({s.rr_t1:g}R) | "
                f"{s.target2:,.2f} ({s.rr_t2:g}R) | {s.quantity} | {_inr(s.capital_at_risk)} | {s.final_score:.0f} |"
            )
        live = [s for s in res.setups if s.today_status]
        if live:
            L += ["", "### Live status today", ""] + [f"- **{s.symbol}** — {s.today_status}" for s in live]
        L += ["", "**Execution rules:** buy-stop at the entry trigger · skip if the open gaps above the "
              "“don't chase” price · cancel if not triggered within "
              f"{res.setups[0].valid_sessions} sessions · exit if T1 isn't hit within "
              f"{res.setups[0].time_stop_sessions} sessions · book part at T1, trail the stop to entry.", ""]
        risk_lines = [(s.symbol, x) for s in res.setups for x in s.risks]
        if risk_lines:
            L += ["### Risk notes", ""] + [f"- **{sym}** — {x}" for sym, x in risk_lines] + [""]
    else:
        L += ["_No setups passed every gate today. Cash is a position._", ""]

    if res.watchlist:
        L += [f"## Watchlist ({len(res.watchlist)})", "", "| Symbol | Action | Setup | Score | Why not a setup |",
              "|---|---|---|---:|---|"]
        for c in res.watchlist:
            L.append(f"| {c.symbol} | {c.action} | {c.setup_type or '—'} | {c.final_score:.0f} | {c.rejected_reason or '—'} |")
        L.append("")

    if res.sectors is not None and not res.sectors.empty:
        top = res.sectors.head(5)
        L += ["## Strongest sectors", "", "| Sector | 3M median % | Breadth % | Score |", "|---|---:|---:|---:|"]
        for _, row in top.iterrows():
            L.append(f"| {row['sector']} | {row['median_ret_63d_pct']:+.1f} | {row['breadth_pct']:.0f} | {row['sector_score']:.0f} |")
        L.append("")

    bad = [q for q in res.quality if q.status == "FAIL"]
    if bad:
        L += [f"## Data quality — {len(bad)} excluded", ""]
        L += [f"- {q.symbol}: {', '.join(q.issues)}" for q in bad[:20]]
        L.append("")

    L += ["---", f"Sources: {', '.join(res.data_sources)}", "", f"> {res.disclaimer}", ""]
    return "\n".join(L)


def write_markdown(res: RunResult, path: Path) -> Path:
    path.write_text(render_markdown(res), encoding="utf-8")
    return path
