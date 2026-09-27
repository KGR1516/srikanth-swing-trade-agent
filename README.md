# Srikanth Swing Trade Agent

A transparent NSE swing-trading **research** agent. Every weekday after market close it scans the universe, validates the data, scores each stock on technical, fundamental, sector and event evidence, applies risk rules, and publishes **next-session setups** (entry trigger, stop loss, targets, R:R, quantity) as Excel, JSON and Markdown.

> It does **not** place broker orders. Output is decision support, not investment advice.

## Pipeline

```
① SCHEDULE        GitHub Actions, 10:30 and 14:45 IST sharp, Mon–Fri
② MARKET DATA     Yahoo Finance adjusted OHLCV  +  NSE bhavcopy close cross-check
③ DATA QUALITY    history length · staleness · OHLC sanity · unadjusted splits · zero volume · NSE mismatch
④ FULL SCANNER    Technical (liquidity, breakout + grade, pre-breakout, pullback, momentum, RS 3M+1M)
                  Confluence (≈370 pandas-ta-classic indicator columns → one bullish %)
                  Fundamental (ROE, D/E, growth, margin, P/E, loss-making)  ·  Sector strength
⑤ EVENT CHECK     results-date blackout · ex-dividend warning
⑥ RISK ENGINE     market regime → risk multiplier · volatility limits · risk budget
⑦ SETUP ENGINE    entry trigger · stop · T1/T2 · R:R · qty · portfolio & sector caps
⑧ FINAL REPORT    swing_agent.xlsx · swing_agent.json · swing_agent.md (+ optional Email / Telegram)
```

## Project layout

```
config/
  settings.yaml           run, data-quality, scanner and scoring settings
  financial_rules.yaml    risk, regime, setup, fundamental and event rules
  universe.yaml           symbols (map SYMBOL: sector, or a plain list) or a live NSE index
src/
  main.py                 CLI  →  python -m src.main scan
  config.py · models.py
  agent/                  orchestrator + one agent per stage
    market_agent · technical_agent · fundamental_agent · event_agent
    risk_agent · setup_agent · report_agent · orchestrator
  data/                   nse (universe, bhavcopy) · market_data · fundamentals
                          corporate_actions · quality
  scanner/                breakout (+pre-breakout) · momentum (+pullback)
                          relative_strength · liquidity
  analysis/               technical indicators · fundamental score · sector strength
                          risk · setup · scoring
  reports/                excel · json · markdown
tests/                    offline unit + end-to-end tests (synthetic data)
data/output/              committed daily reports (history/YYYY-MM-DD/ archive)
```

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pytest -q                          # offline test-suite
python -m src.main scan            # full run
python -m src.main scan --universe nifty200 --capital 1000000 -v
python -m src.main scan --symbols RELIANCE,TCS,IRCTC     # quick check of a few names
python -m src.main scan --no-confluence                  # faster run, skips the indicator catalogue
```

To scan your own list, put one symbol per line in `data/input/watchlist.txt`. If the file has any symbols, they replace the configured universe.

## How a setup is built

| Step | Rule (defaults, all configurable) |
|---|---|
| Liquidity | price ≥ ₹50, 20D avg volume ≥ 2.5 L, avg traded value ≥ ₹10 Cr |
| Setup type | **Breakout**: close > prior 20D high, volume ≥ 1.5× avg, ≤ 5% extended, strong close, close > EMA50 > EMA200 · **Pre-Breakout**: ≤ 3% below 20D high, base ≤ 12% deep, volume drying up · **Pullback**: uptrend, within 2% of EMA20, RSI 40–55, bounce |
| Technical score | 50% setup quality + 50% momentum template (EMA stack, EMA200 rising, RSI, ADX, MACD, % of 52W high, Supertrend, OBV); +5 for a Strong Fresh breakout, +3 for Fresh |
| Breakout grade | **Strong Fresh** ≤1% above level, vol ≥5×, RSI <70 · **Fresh** ≤2%, vol ≥5× · **Solid** ≤3%, vol ≥3×, RSI <75 · **Extended** >5% or RSI ≥75 (sent to the watchlist) · **Unconfirmed** above the level but on light volume |
| Relative strength | percentile = 70% × 63-day rank + 30% × 20-day rank vs Nifty; < 65 is rejected |
| Confluence | share of ~370 indicator readings that are bullish, pooled by category (trend, momentum, moving averages, volume, candles) |
| Final score | 34% technical · 17% RS · 17% fundamental · 15% confluence · 8.5% sector · 8.5% event, then penalties (loss-making −8, failed breakout −15); must be ≥ 60 |
| Verdict | BUY NOW ≥ 80 · BUY ≥ 65 · WATCH ≥ 50 · AVOID ≥ 35 · SKIP. A stock that passes every gate is at least BUY; a rejected stock is never shown as BUY |
| Entry trigger | buy-stop above the day's high (Breakout/Pullback) or the 20D high (Pre-Breakout) + 0.1 × ATR |
| Stop loss | tighter of swing-low (10 bars) and 1.5 × ATR; at least 1.5% and 1 × ATR; rejected if > 8% |
| Targets | T1 = 2R, T2 = 3R; flags if the 52W high sits inside T1 |
| Size | 1% of capital × regime multiplier ÷ risk per share, capped at 15% of capital |
| Regime | Bullish ×1.0 · Neutral ×0.5 · Bearish ×0 (no new longs) — from Nifty vs EMA50/200 and breadth |
| Portfolio | max 6 setups, max 2 per sector |
| Events | results within 5 days → excluded; ex-dividend within 3 days → warning |

**Execution rules shown in every report:** place a buy-stop at the trigger; skip if the stock opens above the "don't chase" price (trigger + 2%); cancel if it hasn't triggered within 2 sessions; exit if T1 isn't reached within 5 sessions.

## Reports (`data/output/`)

- **swing_agent.xlsx** has nine sheets:
  - Summary
  - Next Session Setups (with a colour-coded verdict and breakout grade)
  - Watchlist (near-misses with the reason each was rejected)
  - Score Breakdown (points from each component, penalties, and confluence by category)
  - Pre-Market Checklist (tick-boxes per setup)
  - Full Scan (every stock, including Supertrend, Bollinger %B, Stochastic, VWAP distance and follow-through)
  - Sectors
  - Evidence (every rule check, pass or fail)
  - Data Quality
- **swing_agent.json** is the same content in machine-readable form.
- **swing_agent.md** is a quick summary, also shown as the GitHub Actions job summary.

## GitHub Actions

`.github/workflows/daily-swing-agent.yml` runs twice every weekday:

| Run | Starts | What it does |
|---|---|---|
| Morning | **10:30:00 IST** | Setups from the previous day's completed candle, plus a live status for each one today: triggered, not triggered yet, skipped (opened above the don't-chase price), T1 hit, or stopped out |
| Afternoon | **14:45:00 IST** | A **provisional** scan on today's candle, with today's volume projected to a full day. Use it to spot breakouts before the 15:30 close. The report is labelled provisional because the candle isn't final |

GitHub often starts scheduled jobs 5–20 minutes late. To make the start time exact, each cron fires 20 minutes early. The job installs, runs the tests, then waits until exactly 10:30:00 or 14:45:00 IST before scanning. If GitHub is more than 20 minutes late, the run starts straight away and the job log shows a warning. The report is ready and emailed about 2–4 minutes after the start.

Each run uploads the reports as an artifact and commits them to `data/output/`. It also keeps a copy per run type in `data/output/history/<date>/`. You can run it by hand from **Actions → Daily Swing Trade Agent → Run workflow**, and choose the run type (auto, morning, afternoon or eod) and the universe.

From the command line, use `python -m src.main scan --session morning|afternoon|eod`. The default, `auto`, picks the run type from the IST clock.

- Optional email with the Excel report attached: add the secrets `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` (a Gmail App Password) and `RECIPIENT_EMAIL`. These are the same secrets srikanth-stock-2 uses.
- Optional Telegram alert: add the repository secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
- If fewer than 50% of symbols have usable data (provider outage or holiday), the run **aborts with exit code 2**. The job goes red and yesterday's report is left untouched.

## Merged from srikanth-stock-2

| From stock-2 | Where it lives now |
|---|---|
| Setup grades (Strong Fresh / Fresh / Solid / Extended / Failed), live status, follow-through | `src/scanner/breakout.py` |
| 60-day closing-high "major level" | `bo:clears_60d_closing_high` evidence and a risk note |
| Full pandas-ta-classic confluence voting | `src/analysis/confluence.py` (ported as is), weight 15% |
| Bollinger %B, Stochastic, OBV, VWAP, Supertrend | `src/analysis/technical.py` (pure pandas, no TA-Lib needed) |
| 20-day RS vs Nifty | blended into the RS percentile |
| Action bands BUY NOW → SKIP | `scoring.action_bands` in `settings.yaml` |
| Loss-making −8 and failed-breakout −15 penalties; P/E caution | `scoring.penalties`, `fundamentals.pe_caution` |
| Watchlist file and full-NSE universe | `data/input/watchlist.txt`, `source: full_nse` |
| Gmail report email | `src/agent/report_agent.py` |
| Score Breakdown and Pre-Market Checklist sheets | `src/reports/excel.py` |
| `--symbols` CLI option | `src/main.py` |

**Changed on purpose:**

- **Stops and position size:** stock-2 used a fixed 1.5% stop and "5–7% of capital" sizing bands. Here the stop is based on ATR and the swing low, and quantity is sized from risk.
- **Grade names:** stock-2 labelled a low-volume breakout "Extended". Here it's "Unconfirmed".
- **One run a day:** stock-2 ran three times a day (pre-market, mid-session, post-close). This agent works on completed daily candles, so it runs once after the close. A manual run during market hours ignores the day's unfinished candle.

## Data notes

- Yahoo Finance is used for adjusted history, fundamentals and results dates. Missing fundamentals or results dates give **neutral** scores, never bullish ones, and a missing results date adds a "verify on NSE" risk note.
- NSE's bhavcopy is used to cross-check the last close when it's reachable. NSE often blocks cloud IPs, so this is best-effort.
- For production, replace `src/data/market_data.py` with an authorised broker or vendor feed. The analysis layer only needs `{symbol: DataFrame[Open, High, Low, Close, Volume]}`.
