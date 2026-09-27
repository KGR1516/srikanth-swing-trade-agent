"""CLI entry point:  python -m src.main scan [--capital 500000] [--universe nifty200]"""
from __future__ import annotations

import logging
from typing import Optional

import typer
from dotenv import load_dotenv

from src.agent.orchestrator import DataUnavailableError, run_scan
from src.config import load_config

app = typer.Typer(add_completion=False, help="NSE swing-trade research agent (no broker orders).")


@app.callback()
def _root() -> None:
    """Srikanth Swing Trade Agent."""


@app.command()
def scan(
    capital: Optional[float] = typer.Option(None, help="Override trading capital (INR)."),
    universe: Optional[str] = typer.Option(
        None, help="static | nifty50 | nifty100 | nifty200 | nifty500 | full_nse"),
    symbols: Optional[str] = typer.Option(
        None, "--symbols", "-s", help="Comma-separated symbols to scan instead of the universe, e.g. RELIANCE,TCS"),
    top_n: Optional[int] = typer.Option(None, help="Max setups in the report."),
    no_confluence: bool = typer.Option(False, "--no-confluence", help="Skip the full-indicator confluence step."),
    session: str = typer.Option(
        "auto", help="auto | morning (setups + live status) | afternoon (provisional, today's candle) | eod"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run the full pipeline and write Excel / JSON / Markdown reports."""
    load_dotenv()
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    cfg = load_config()
    if capital:
        cfg.settings["capital"]["total"] = capital
    if universe:
        cfg.universe["source"] = universe
    if top_n:
        cfg.settings["report"]["top_n"] = top_n
    if symbols:
        cfg.universe["symbols"] = [x.strip().upper() for x in symbols.split(",") if x.strip()]
        cfg.universe["source"], cfg.universe["watchlist_file"] = "static", None
    if no_confluence:
        cfg.settings.setdefault("confluence", {})["enabled"] = False

    try:
        res = run_scan(cfg, session=session)
    except DataUnavailableError as exc:
        typer.secho(f"ABORTED: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    r = res.regime
    typer.echo(f"\n{res.session_label}")
    typer.echo(f"Regime {r.regime.upper()} (risk ×{r.exposure:g}) · breadth {r.breadth_pct:.0f}%")
    typer.echo(f"Completed scan: {len(res.setups)} next-session setups, {len(res.watchlist)} on watchlist.")
    for s in res.setups:
        typer.echo(f"  {s.rank}. {s.symbol:<12} {s.action:<8} {s.setup_type:<12} entry ≥ {s.entry_trigger:>10,.2f}  "
                   f"SL {s.stop:>10,.2f}  T1 {s.target1:>10,.2f}  T2 {s.target2:>10,.2f}  qty {s.quantity}")
        if s.today_status:
            typer.echo(f"       today: {s.today_status}")


if __name__ == "__main__":
    app()
