# Srikanth Swing Trade Agent

A transparent NSE swing-trading **research** agent. Every weekday after market close it scans the universe, validates the data, scores each stock on technical, fundamental, sector and event evidence, applies risk rules, and publishes **next-session setups** (entry trigger, stop loss, targets, R:R, quantity) as Excel, JSON and Markdown.

> It does **not** place broker orders. Output is decision support, not investment advice.

## Pipeline

```
① SCHEDULE        GitHub Actions, 17:00 IST Mon–Fri
② MARKET DATA     Yahoo Finance adjusted OHLCV  +  NSE bhavcopy close cross-check
③ DATA QUALITY    history length · staleness · OHLC sanity · unadjusted splits · zero volume · NSE mismatch
④ FULL SCANNER    Technical (liquidity, breakout, pre-breakout, pullback, momentum, RS)
                  Fundamental (ROE, D/E, growth, margin, P/E)  ·  Sector strength
⑤ EVENT CHECK     results-date blackout · ex-dividend warning
⑥ RISK ENGINE     market regime → risk multiplier · volatility limits · risk budget
⑦ SETUP ENGINE    entry trigger · stop · T1/T2 · R:R · qty · portfolio & sector caps
⑧ FINAL REPORT    swing_agent.xlsx · swing_agent.json · swing_agent.md (+ optional Telegram)
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
```

## How a setup is built

| Step | Rule (defaults, all configurable) |
|---|---|
| Liquidity | price ≥ ₹50, 20D avg volume ≥ 2.5 L, avg traded value ≥ ₹10 Cr |
| Setup type | **Breakout**: close > prior 20D high, volume ≥ 1.5× avg, ≤ 5% extended, strong close, close > EMA50 > EMA200 · **Pre-Breakout**: ≤ 3% below 20D high, base ≤ 12% deep, volume drying up · **Pullback**: uptrend, within 2% of EMA20, RSI 40–55, bounce |
| Technical score | 50% setup quality + 50% momentum template (EMA stack, EMA200 rising, RSI, ADX, MACD, % of 52W high) |
| Relative strength | 63-day excess return vs Nifty, ranked as a percentile; < 65 is rejected |
| Final score | 40% technical · 20% RS · 20% fundamental · 10% sector · 10% event, and must be ≥ 60 |
| Entry trigger | buy-stop above the day's high (Breakout/Pullback) or the 20D high (Pre-Breakout) + 0.1 × ATR |
| Stop loss | tighter of swing-low (10 bars) and 1.5 × ATR; at least 1.5% and 1 × ATR; rejected if > 8% |
| Targets | T1 = 2R, T2 = 3R; flags if the 52W high sits inside T1 |
| Size | 1% of capital × regime multiplier ÷ risk per share, capped at 15% of capital |
| Regime | Bullish ×1.0 · Neutral ×0.5 · Bearish ×0 (no new longs) — from Nifty vs EMA50/200 and breadth |
| Portfolio | max 6 setups, max 2 per sector |
| Events | results within 5 days → excluded; ex-dividend within 3 days → warning |

**Execution rules shown in every report:** place a buy-stop at the trigger; skip if the stock opens above the "don't chase" price (trigger + 2%); cancel if it hasn't triggered within 2 sessions; exit if T1 isn't reached within 5 sessions.

## Reports (`data/output/`)

- **swing_agent.xlsx** has seven sheets: Summary, Next Session Setups, Watchlist (near-misses with the reason each was rejected), Full Scan, Sectors, Evidence (every rule check, pass or fail) and Data Quality.
- **swing_agent.json** is the same content in machine-readable form.
- **swing_agent.md** is a quick summary, also shown as the GitHub Actions job summary.

## GitHub Actions

`.github/workflows/daily-swing-agent.yml` runs at 17:00 IST on weekdays. It runs the tests, then the agent, uploads the reports as an artifact and commits them to `data/output/`. You can also run it manually from **Actions → Daily Swing Trade Agent → Run workflow**, with an optional universe override.

- Optional Telegram alert: add the repository secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
- If fewer than 50% of symbols have usable data (provider outage or holiday), the run **aborts with exit code 2**. The job goes red and yesterday's report is left untouched.

## Data notes

- Yahoo Finance is used for adjusted history, fundamentals and results dates. Missing fundamentals or results dates give **neutral** scores, never bullish ones, and a missing results date adds a "verify on NSE" risk note.
- NSE's bhavcopy is used to cross-check the last close when it's reachable. NSE often blocks cloud IPs, so this is best-effort.
- For production, replace `src/data/market_data.py` with an authorised broker or vendor feed. The analysis layer only needs `{symbol: DataFrame[Open, High, Low, Close, Volume]}`.
