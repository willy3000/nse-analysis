# NSE Intelligence Platform: User Guide

This guide covers how the system works end to end, how to run it, how to read every screen and signal, and how to turn what you see into disciplined investment decisions.

> **Important.** This is a research and decision-support tool. It never places trades. Its signals describe what happened in historical data, and none is a prediction or a guarantee. Read section 9 before acting on anything.

---

## 1. How the system works, end to end

```
MyStocks Africa API (15-min delayed NSE data)      Licensed CSV files (optional)
            │                                                │
            └──────────────► Provider adapters ◄─────────────┘
                                   │  translate provider formats into one internal format
                                   ▼
                        Validation & normalisation
                                   │  reject bad prices, flag anomalies, never invent values
                                   ▼
                        PostgreSQL database
                                   │  raw prices kept forever; corrections archived, not overwritten
                                   ▼
                        Analytics engine (worker)
                                   │  indicators → trends → levels → signals → scores → outcomes
                                   ▼
                        FastAPI backend  ──►  Next.js web app (what you see)
```

**Three processes run.**

| Process | Job |
|---|---|
| **Worker** (`nse_platform.worker.main`) | Fetches data on a schedule, runs all analytics, evaluates alerts, runs backtests. |
| **API** (port 8000) | Serves precomputed results to the browser. It never contacts the data provider itself, so the website stays fast and keeps working if the provider is down. |
| **Web app** (port 3000) | The dashboard. All traffic goes through the API; the browser never talks to the provider or the database. |

### 1.1 Where the data comes from
- **MyStocks Africa Partner API** (your sandbox key). This is the current real-data source. It covers 65 NSE securities, including 2 ETFs and 3 REITs, with daily history from June 2016.
  - The data is **delayed by 15 minutes** during trading hours.
  - Outside trading hours it is the **end-of-day** close.
  - The platform never labels this data "Live".
- **Not available from this provider:** financial statements, official NSE index values (NASI, NSE 20/25), bond prices and, in the sandbox, dividend history. The app shows these as *unavailable*, never as zero. You can add them by importing licensed or published files (section 8).
- **Synthetic development data:** fictitious instruments with `ZZ` symbols, used only for testing. A yellow banner appears whenever you are viewing it. Never base decisions on it.

### 1.2 How data is processed
1. **Validation.** Prices that are zero or negative, have future dates, have impossible high/low ranges, or are duplicated are rejected or flagged. Moves of more than 50% in a day are flagged *suspect* unless a corporate action explains them.
2. **Missing data stays missing.** If the provider did not report a bar's open, high or low, those values are left empty. Range-based indicators (ATR, ADX, swing levels) then skip that bar, and the chart switches to a close-only line.
3. **Corporate-action adjustment.** Bonus issues and splits are applied as adjustment factors when charts and indicators are computed. The original prices are kept. You can switch any chart between **Adjusted** and **Raw**.
4. **Point-in-time rule.** Every indicator, signal and fundamental metric for a given date uses only information available on that date. This prevents look-ahead bias and is enforced by automated tests.

---

## 2. How often data refreshes

Times are East Africa Time (EAT). NSE continuous trading runs **09:30 to 15:00, Monday to Friday**, excluding public holidays.

| When | What the worker does |
|---|---|
| **Every 15 minutes, Mon–Fri 09:00–15:45** | Polls quotes for all instruments. These are 15-minute delayed, so polling more often adds nothing. |
| **Mon–Fri 16:30** | Downloads the day's end-of-day bars (re-checking the last 10 days for provider corrections). Then it recomputes **all analytics** (indicators, trends, support/resistance, signals, scores, fundamentals, signal outcomes) and evaluates **alerts**. |
| **Mon–Fri 17:00** | Corporate data: corporate actions, company profiles, company news, market intel. |
| **Sunday 02:00** | Re-syncs the instrument list (new listings, suspensions), replays signal history to refresh base rates, and re-runs score calibration. |
| **Every 5 seconds** | Checks the backtest queue. |

**In the browser:**
- Market status and notifications refresh every 60 seconds, and the data-health badge every 2 minutes.
- When the worker finishes a run, the app receives an event and reloads its data automatically.

**What this means for you:** the analytics (trends, signals, scores) are **end-of-day analytics**. They update once per trading day, at about 16:30. Intraday quotes update every 15 minutes, delayed, and only change the displayed price; they do not change the signals until the evening run. The platform is designed for **swing and position decisions (days to months)**, not day trading.

> The worker must be running for any of this to happen. Start everything with `bash scripts/dev.sh`. If only the API and web app are running, you will see the last loaded data, and freshness badges will turn to *Stale* as days pass.

---

## 3. Running the system

```bash
bash scripts/dev.sh        # starts database, migrations, API, worker and web app
```
Then open **http://localhost:3000** and sign in (or create a local account).

Useful operator commands, run from `services/api` with the virtual environment:

| Command | Purpose |
|---|---|
| `python -m nse_platform.cli sync-instruments --provider mystocks` | Refresh the instrument list |
| `python -m nse_platform.cli backfill --provider mystocks --years 11` | Full history reload |
| `python -m nse_platform.cli ingest-eod` | Fetch the latest daily bars now |
| `python -m nse_platform.cli analytics` | Recompute analytics now (add `--replay` to rebuild signal history) |
| `python -m nse_platform.cli validate-scores` | Re-run score calibration |
| `python -m nse_platform.cli import-csv prices FILE.csv` | Import licensed price files (also `financials`, `corporate-actions`, `index`) |

Your API key lives in `.env` at the repository root. That file is git-ignored; never commit or share it.

---

## 4. The data labels you must understand

Every price carries a **freshness badge**. Check it before trusting a number.

| Badge | Meaning |
|---|---|
| **Delayed** | During market hours: at least 15 minutes old. |
| **End of day** | Market closed: the official close of the last session. |
| **Historical** | Archival or synthetic data, not a current price. |
| **Stale** | Older than it should be. Hover for the reason (below). |
| **Unavailable** | No observation from any source. |
| **Live** | Real-time. **No connected provider offers this**, so you will not see it. |

Reasons a price can be *Stale*:
- **No recent trade:** many NSE stocks are illiquid, and no shares changed hands.
- **Suspended:** trading in the security is halted.
- **Feed failure:** the provider is down or erroring. Check the Data Centre.
- **Missing provider data:** the provider has not supplied the latest session.

The **Data** badge in the top bar summarises everything:
- **COMPLETE:** all instruments are current.
- **PARTIAL:** some instruments are behind.
- **DEGRADED:** the provider is unhealthy.
- **UNAVAILABLE:** no real data is loaded.

---

## 5. Screen-by-screen guide

| Section | Use it to |
|---|---|
| **Overview** | Read the market's mood: advancers versus decliners, gainers, losers, most active, unusual volume (2× or more of average), strongest bullish and bearish trends, new signals from the last 7 days, upcoming corporate events, your portfolio and watchlists. |
| **Market Scanner** | Filter all instruments by price, volume, relative volume, change, RSI, trend, volatility, dividend yield, the three scores and trading frequency. Sort any column and save filter sets. An instrument missing a filtered value is excluded, not treated as zero. |
| **Surge Radar** | Stocks surging *right now* (or set up for a breakout), each with a Surge Sheet: your position cost, break-even, what happened after similar past surges in KES, stop, liquidity and data-quality flags. Flagged stocks are added automatically to **Surge Watch (auto)** and you get a notification. See section 6.6. |
| **Opportunities** | See every active signal with its evidence, invalidation level, risk factors and historical base rates. Click a row for the full detail (section 6). |
| **Trends** | See each instrument's trend on daily, weekly and monthly timeframes side by side. Expand **Evidence** to see why it was classified that way. |
| **Instruments** | Browse the catalogue. Open any symbol for the full research page: chart (D/W/M, adjusted or raw, moving averages, Bollinger bands, support/resistance), multi-timeframe trend, all indicators, the three score breakdowns, performance, risk, liquidity, fundamentals, dividends, events, news, signal history and data coverage. |
| **Fundamentals** | Compare valuation, profitability and dividend metrics across companies, where data exists. |
| **Strategy Lab** | Build rule-based strategies and backtest them with real NSE costs (section 7). |
| **Paper Portfolio** | Simulate buys, sells and dividends with realistic fees, and track profit and loss, allocation, concentration, and performance against a benchmark. |
| **Watchlists** | Track instruments with your own entry, target and stop reference levels, tags and notes. |
| **Alerts** | Get in-app notifications for price levels, RSI, moving-average crosses, volume spikes, new signals, dividends, announcements, risk-score jumps and changes in fundamental metrics. |
| **News & Events** | Read headlines from the provider, linked to instruments, and a calendar of corporate events. No AI summaries or sentiment scores are generated. |
| **Data Centre** | Check provider health, rate limits, coverage per instrument, every ingestion run, and data-quality issues. |
| **Settings** | See the full score methodology, the **score calibration results** and the fee schedule. |

---

## 6. How to read the signals

### 6.1 Anatomy of a signal
Every signal on the Opportunities page shows:
- **Signal type and category**: momentum, breakout, reversal, pullback, value, dividend or risk.
- **Direction**: bullish, bearish or neutral (informational risk).
- **Timeframe**: `1d` (daily bars) or `1w` (weekly bars). Weekly signals are slower but less noisy.
- **Reference price**: the close on the bar where the signal fired.
- **Supporting evidence**: the exact indicator values that triggered it.
- **Invalidation**: the price that would prove the signal wrong, for example "daily close below SMA50 (KES 31.20)". This is your natural stop-loss reference.
- **Risk factors**: illiquidity, high volatility, missing OHLC, deep drawdown, overbought conditions, and a reminder about costs.
- **Expiry**: signals expire after a set number of bars. Old signals lose relevance.
- **Status**: Active, Invalidated (the price hit the invalidation level) or Expired.
- **Historical behaviour**: the base-rate table (6.3).

### 6.2 What each signal means

| Signal | Plain meaning | Typical use |
|---|---|---|
| **Momentum strong** | Up over 10% in 60 bars, still rising, price above rising averages, above-average activity | Trend-following entry candidate |
| **MA alignment** | SMA20 > SMA50 > SMA200 has just formed | Confirms a maturing uptrend |
| **Breakout (unconfirmed volume)** | Closed above the 20-bar closing high on ordinary volume | Watch only; weaker than the volume-confirmed version |
| **Volume-confirmed breakout** | Same breakout on 1.5× or more of average volume | Stronger breakout candidate |
| **52-week high breakout** | Closed above the highest close of the past year | Strength signal; often continues, sometimes reverses sharply |
| **Consolidation breakout** | Price was in a tight range (Bollinger squeeze) and broke upward | Early move out of a quiet period |
| **Oversold recovery** | RSI(14) crossed back above 30 | Possible bounce; low reliability in strong downtrends |
| **Bullish / bearish RSI divergence** | Price made a new low (high) but momentum did not | Early warning of a possible turn |
| **Golden / death cross** | SMA20 crossed above (below) SMA50 | Trend transition; slow and lagging |
| **Pullback to EMA20** | Uptrend intact, price dipped to its 20-bar average and held | Entry within an existing uptrend at a better price |
| **Support retest** | Rising trend, price tested its last swing low and held | Entry near a defined risk level |
| **Value discount** | P/E at least 25% below the sector median, with ROE above 10% and growing earnings | Fundamental idea; needs financial statements on file |
| **High dividend yield** | Trailing yield of 6% or more, at least 3 years of dividends, payout below 90% | Income idea |
| **Upcoming ex-dividend** | Ex-date within 30 days | Timing information. The price normally drops by about the dividend on the ex-date. |
| **Risk alerts**: volatility spike, liquidity deterioration, significant drawdown (more than 30%), bearish momentum, price gap, trading suspended, data quality | Things that should make you cautious | Review positions; do not treat these as buy signals |

### 6.3 Reading the base-rate table, the most important part
For each signal type, the platform records what happened 5, 10, 20 and 60 bars after **every past occurrence**, using a look-ahead-free historical replay.

| Column | Meaning |
|---|---|
| **n** | Number of past occurrences. **Under 30 is shown faded.** Treat it as anecdotal. |
| **Mean** | Average return after that many bars. |
| **Baseline** | Average return over the same period for *all* bars of the same stocks. |
| **Hit rate** | Share of occurrences that moved in the signal's direction. |

**How to judge it:** a signal is only interesting if its **mean clearly beats the baseline**, with a **large n**, by **more than about 4.2%**. That is the round-trip trading cost on the NSE: about 2.08% to buy plus 2.08% to sell. A signal averaging +2% over 20 bars *loses money after costs*. Overlapping signals are not independent, so even good-looking numbers overstate certainty.

### 6.4 The three scores (0–100)
- **Trading Opportunity Score.** Technical attractiveness relative to the rest of the market: momentum (20%), trend (25%), relative volume (10%), closeness to the 52-week high (15%), relative strength (15%), liquidity (15%).
- **Investment Quality Score.** Business quality and valuation: growth, margins, ROE, leverage, cash conversion, P/E, P/B, dividend yield and consistency. It shows *n/a* until financial statements are imported, because the provider does not supply them.
- **Risk Score. Higher means riskier.** Volatility, drawdown, illiquidity, price gaps, leverage, corporate events and data reliability.

These scores are **rankings within the market today, not forecasts**. Open any instrument to see each component, its weight and its raw value. Faded or struck-through components were unavailable and excluded.

**Calibration result on real NSE data (Settings page):** across 2016–2026, the Trading Opportunity Score showed **no reliable predictive value**. The average correlation with next-month returns was **−0.10**. Use it to *describe* which stocks currently look technically strong. **Do not** buy simply because a score is high. Re-check the calibration after each weekly run.

### 6.5 Trend classes
In order of decreasing strength:
1. Strong bullish
2. Emerging bullish
3. Bullish pullback
4. Potential reversal
5. Sideways
6. Weakening bullish
7. Emerging bearish
8. Strong bearish

*Insufficient data* means there are fewer than 60 bars. Expand **Evidence** to see the moving-average structure, slopes, RSI, MACD, swing structure, trend strength (ADX) and a liquidity warning.

**Timeframes disagree on purpose.** A stock can be bullish on daily bars while bearish on weekly bars, which usually means a short rally inside a longer decline.

### 6.6 Surge Radar and the Surge Sheet

**What it detects**

| Signal | Rule | When it fires |
|---|---|---|
| **Intraday surge** | Delayed quote at least 8% above the last close, with session volume already at least 3× the 20-day average | During the session, at each 15-minute quote poll (about 15–30 minutes after the move, because the data is delayed) |
| **Price & volume surge** | Close at least 8% up, volume at least 3× average, and a move of at least 2.5 standard deviations for *this* stock | At the 16:30 end-of-day run |
| **Pre-breakout squeeze** | Tightest price range of the last 120 bars, volume building, price within 3% of its 20-day high | At the 16:30 run; a *setup*, not a move yet |

Each new flag adds the stock to **Surge Watch (auto)**, with the stop as the watchlist stop level, and sends you a notification.

**What it cannot do:** no system can see a spike before it starts. Olympia Capital (OCH) on 28 Oct 2026 is a good example:
- The day before was quieter than usual.
- On the day it closed +273% on about 40× normal volume (its reported high of 150 is almost certainly a bad print).
- It then fell about 10% a day; buying at the 28 Oct close would have lost about 56% by 6 Nov.

The radar would have flagged it intraday on 28 Oct. The Surge Sheet exists to show you that, after surges, collapses are about as common as continuations.

**Reading the Surge Sheet**
1. **Your position.** Enter a budget or a share count. The sheet shows the cost, the fees (about 2.08%) and the **break-even** price, about +4.2% above entry once both sides' fees are paid.
2. **What happened after similar surges.** The bad, weak, typical, good and great scenarios are the 10th, 25th, 50th, 75th and 90th percentiles of past outcomes, converted into **your net KES profit or loss after fees**, for holding 1, 5, 10 or 20 sessions. Check **n** (under 30 means anecdotal) and **Profitable after costs**.
3. **If the current trend continued.** This is a hypothetical (capped at the NSE's roughly 10% daily limit), shown together with how often past surges actually did that. It is usually rare.
4. **Exit if wrong.** The stop is the pre-surge close, meaning the surge fully reversed, and the sheet shows your loss if it's hit. Gaps can make the loss larger.
5. **Can you actually trade it?** This shows how many sessions it would take to buy and sell your size at 10% of daily volume. Thin NSE stocks can trap you.
6. **Before you act.** A checklist, plus data-quality flags (for example a suspected bad print).

**A sensible way to use it:** treat a surge as a prompt to *investigate*, not to buy.
- Look for the cause (News & Events).
- Prefer surges with news, adequate liquidity, and a *Typical* scenario that clears break-even.
- If you act, size the position so the stop loses at most 1–2% of your capital, and decide the take-profit before you enter.
- Chasing a stock already 20–30% up is where most of the losses in the Olympia example came from.

---

## 7. Testing ideas in the Strategy Lab
1. Pick a template or build rules. **Entry:** all conditions must hold. **Exit:** any condition triggers. You can add a stop loss, take profit, maximum holding period and position sizing.
2. Set a **minimum trading frequency** (0.5 or more is recommended) so you exclude stocks that rarely trade.
3. Run the backtest. The simulator:
   - acts on **the next session's open**, never on the signal bar;
   - pays the **full NSE fee schedule** on both sides, plus slippage (25 bps by default);
   - fills at most **10% of the day's volume**, with no fill on days without trades;
   - applies bonus issues and splits, and credits dividends net of the 5% withholding tax.
4. **Judge only the Out-of-sample column.**
   - The history is split chronologically: about 60% *in-sample*, 20% *validation*, 20% *out-of-sample*.
   - If results are good in-sample but poor out-of-sample, the rules are **overfit**.
   - Fewer than 30 trades means the results are not statistically meaningful.
   - Read every yellow warning.
5. Compare against the **benchmark**, an equal-weight buy-and-hold of the universe. A strategy that does not beat simply holding the market after costs adds no value.
6. Every run stores a data fingerprint and the version of each component, so you can reproduce it exactly. Re-running the same idea many times with small tweaks is itself a form of overfitting; the app warns you after five runs.

---

## 8. Improving the data
- **Fundamentals:** import published annual-report figures with `import-csv financials`. The publication date is required, which keeps backtests honest. This activates P/E, ROE, value signals and the Investment Quality Score.
- **Indices:** import NASI or NSE 20 history with `import-csv index` to use a real index as the benchmark.
- **Better data:** NSE Data Services (`dataservices@nse.co.ke`) sells licensed end-of-day and real-time data. See `docs/data-sources/provider-research.md`.

---

## 9. A disciplined decision process

No tool can promise profit. What it can do is help you make **fewer, better-researched decisions with defined risk**. A suggested routine follows.

### Step 1: Check the data (1 minute)
The Data badge should read COMPLETE or PARTIAL, and the instrument you care about should show *End of day* or *Delayed*. If it shows *Stale*, find out why before doing anything.

### Step 2: Read the market (Overview)
Breadth tells you whether most stocks are rising or falling. In weak markets, demand stronger evidence before buying.

### Step 3: Shortlist candidates
Use the Scanner or Opportunities. A practical starting filter:
- trading frequency of 60% or more;
- average traded value large enough for your position size;
- Risk Score below about 60;
- a bullish daily trend that the weekly trend does not contradict.

### Step 4: Research each candidate (instrument page)
- **Confirm across timeframes.** Prefer setups where daily and weekly trends agree.
- **Check the base rate.** Does this signal type beat the baseline by more than 4.2% with n of 30 or more? If not, it is a *watch* idea, not a *buy*.
- **Check the fundamentals** where available: profitable, reasonable debt, consistent dividends.
- **Check events and news.** Is there an upcoming ex-dividend date, a suspension or a results date?
- **Check liquidity.** If the stock trades only a few thousand shares a day, you may not be able to sell when you want to.

### Step 5: Define the trade before entering
- **Entry:** the next session. Data and analytics are end-of-day, so you are always acting on yesterday's close.
- **Exit if wrong:** the signal's **invalidation level**, or your own stop.
- **Exit if right:** a target based on resistance levels or a trailing rule.
- **Position size:** risk a small, fixed share of capital per idea. For example, (account × 1%) ÷ (entry − stop) gives the number of shares.
- **Cost check:** the expected move must comfortably exceed about 4.2% round-trip costs.

### Step 6: Rehearse first
Run the idea as a rule in the Strategy Lab and in the Paper Portfolio for several weeks. Put the instrument on a watchlist with your entry, target and stop, and set alerts for those prices and for the invalidation level.

### Step 7: Review
After exits, compare what happened with the signal's base rates. Keep only the setups that keep working for you after costs.

### Rules of thumb
- One signal is not a decision. Look for **agreement** between trend, signal, base rate, liquidity and fundamentals.
- Risk alerts (bearish momentum, drawdown, liquidity deterioration, suspension) are reasons to **reduce or avoid**, not to buy the dip automatically.
- Illiquid NSE stocks can show attractive signals that cannot be traded at the displayed price.
- Dividend capture rarely works: prices drop by about the dividend on the ex-date, and you pay costs and 5% withholding tax.
- Past behaviour does not guarantee future results. Base rates and backtests describe the past only.

---

## 10. Troubleshooting

| Symptom | Check |
|---|---|
| Prices show *Stale* everywhere | Is the worker running? Look at the Data Centre for recent runs and provider errors. |
| Provider shows UNCONFIGURED | `MYSTOCKS_API_KEY` is missing from `.env`. |
| "No real market data" banner | Run `sync-instruments` then `backfill` (section 3). |
| Scores show *n/a* | Too few components are available, usually because fundamentals are missing. |
| Backtest stuck on *queued* | The worker is not running. |
| Yellow synthetic banner | The universe selector is on synthetic data; switch it to *Real*. |
