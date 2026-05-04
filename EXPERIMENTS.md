# Research Log

This document tracks the reasoning, experiments, and results behind the strategies in this project. It is intentionally a living record — hypotheses first, results after.

---

## The Market

Polymarket's BTC 5-minute markets are binary prediction contracts: each window asks whether BTC will close above or below its opening price. 288 windows per day, Chainlink oracle, fully on-chain settlement.

Key properties that shape strategy design:

- **Fixed strike, moving probability.** The reference price is locked at T=0 (Vatic API, sourced from Chainlink). From that moment, the only thing driving UP/DOWN prices is the accumulated BTC drift relative to a fixed number. This is very different from continuous futures markets.
- **Short window, high variance.** 5 minutes is short enough that BTC often reverses after a strong initial move. The market prices this: a $50 spread at T=150 translates to much less than $50 certainty of outcome.
- **Time decay compresses probability.** As the window approaches T=0, the probability distribution collapses. A token that was at $0.60 with 3 minutes left might reach $0.95 with 10 seconds left — or drop to $0.05 if BTC reverses. Both extremes produce tradeable opportunities.
- **Thin liquidity.** $5K–$50K per BTC window. A $3–$10 USDC position has no meaningful market impact, but larger sizes will move prices.
- **Fees are nonlinear.** The taker fee is `0.072 × p × (1-p) × size`, which peaks at p=0.50 (~1.56%) and falls at extremes. This means the cost of entering at maximum uncertainty is highest. Entering when the model has a strong view (price far from 0.50) is cheaper per dollar of expected edge.

---

## Data Collection

Historical data lives in `datasets/` as CSV files, one per window. Each file contains one row per second with:

```
time_left, target_btc, live_btc, spread, up_price, down_price
```

Collection is handled by `data_extract/btc5m_collector.py`. The current dataset covers **481 windows**, which is roughly 1.7 days of continuous data.

---

## The Model: Brownian Motion

### Assumption

Model BTC price as a Brownian motion during the 5-minute window:

```
spread(t) = BTC(t) - target_btc
```

Under a standard Brownian motion with volatility σ, the spread at the end of the window given the current spread `s` and `τ` seconds remaining is normally distributed:

```
spread(T=0) | spread(T=τ) = s  ~  Normal(s, σ²τ)
```

The market settles UP if `spread(T=0) > 0`. Therefore:

```
P(UP  | s, τ) = Φ(s / (σ·√τ))
P(DOWN| s, τ) = 1 − P(UP)
```

where Φ is the standard normal CDF.

### Volatility Calibration

σ = **3.9856** (USD / √second). This was estimated from historical BTC tick data across multiple 5-minute windows. It captures the typical per-second BTC price volatility in USD terms.

In practice, σ · √300 ≈ $69, meaning a ±$69 one-sigma range for the full 5-minute window. At T=150s (halfway), the one-sigma uncertainty is ±$49.

### Edge

The key quantity is the **edge**: the difference between the model probability and the market price.

```
edge_up   = P(UP)   − up_price
edge_down = P(DOWN) − down_price
```

A positive edge means the model thinks the outcome is more likely than the market is pricing. A negative edge means the market has already priced in more confidence than the model assigns.

The model is deliberately simple. It assumes:
1. BTC follows a random walk (no drift during 5 minutes)
2. Historical σ is predictive of future σ
3. Market prices are "fair" as a baseline (i.e. edge is how much *better* our model is, not absolute probability)

These assumptions break in tail events (flash crashes, sudden news), but hold well during ordinary 5-minute windows.

---

## Strategy V1: `BrownianStrategy`

**File:** `strategies/brownian_motion.py`

### Hypothesis

Enter when edge exceeds a minimum threshold (we have a meaningful model advantage over market pricing). Hold until settlement unless a profit-take trigger fires. The profit-take is designed to lock in gains early when the PnL target is met rather than riding all the way to the binary outcome.

### Entry logic

```
if edge_up   > min_edge  →  BUY_UP
if edge_down > min_edge  →  BUY_DOWN
```

### Exit logic

1. **Profit-take** (if configured): close when `position_pnl >= profit_take`. Takes a guaranteed smaller gain.
2. **Settlement**: hold to the end of the window otherwise.

### Parameters used in backtest

```
sigma         = 3.9856
min_edge      = 0.5      # enter only when model probability is 50pp above market
profit_take   = None     # disabled — hold to settlement
min_time_left = 10
max_time_left = 290
```

`min_edge = 0.5` is intentionally high. It means we only enter when we think the outcome is >50 percentage points more likely than the market does. This keeps trade frequency very low (30 trades across 481 windows) and concentrates on the highest-conviction signals.

### Results (481 windows, $3 USDC/trade)

| Metric | Value |
|---|---|
| Trades | 30 |
| Win rate | 50.0% |
| Total PnL | +$71.17 |
| Avg PnL / trade | +$2.37 |
| Max drawdown | $18.00 |

**Analysis.** A 50% win rate sounds like a coin flip, but the average PnL per trade is significantly positive (+$2.37 on $3 positions ≈ +79% per trade). This reflects asymmetric payoffs: when we enter at a large edge, we're buying a token priced far below our model probability — so when we win, we win big; when we lose, the market was right and the model was wrong. The high min_edge filter means we're only entering in genuinely extreme situations.

The $18 max drawdown on a 30-trade sample is high relative to average PnL. This points to the main weakness: **holding to settlement exposes the position to full reversal risk**. A position entered at edge 0.6 might see the edge disappear (market catches up) and then reverse — but the strategy holds through it. This motivated V2.

---

## Strategy V2: `EdgeExitStrategy`

**File:** `strategies/brownian_edge_exit.py`

### Hypothesis

The model edge is valuable information not just at entry but throughout the hold. If the edge on our side turns negative (market has priced in more confidence than our model in the opposite direction), the original reason to hold the position no longer exists. Exit immediately.

### Changes from V1

- **Remove profit-take** — not needed if we exit when the thesis breaks
- **Add edge-gone exit**: when `edge < 0` on the open side, close
- **Add retry guard**: in live trading, close orders can be delayed; if the position is still open after `retry_timeout` seconds, re-issue the close

### Results (481 windows, $3 USDC/trade)

| Metric | Value |
|---|---|
| Trades | 31 |
| Win rate | 58.1% |
| Total PnL | +$89.26 |
| Avg PnL / trade | +$2.88 |
| Max drawdown | $8.60 |

**vs. V1:**

| Metric | V1 (hold to settle) | V2 (edge exit) | Delta |
|---|---|---|---|
| Win rate | 50.0% | 58.1% | +8.1 pp |
| Total PnL | +$71.17 | +$89.26 | +25.4% |
| Avg PnL / trade | +$2.37 | +$2.88 | +21.5% |
| Max drawdown | $18.00 | $8.60 | −52.2% |

**Analysis.** All four metrics improved simultaneously. The win rate jump (+8.1 pp) confirms the hypothesis: when the model edge disappears, staying in the position is not neutral — it's negative EV. Exiting recovers capital that would otherwise be lost on reversals. The max drawdown improvement is particularly significant: cutting it in half is not just a risk metric improvement, it changes the practical viability of running the strategy with real capital.

The slight increase in trade count (30→31) suggests that the edge-exit occasionally closes a position and creates room for a second entry in the same window — but this is a minor effect.

---

## Current Open Questions

1. **Sigma calibration.** The σ=3.9856 is a fixed estimate. Realized intra-window volatility varies significantly with macro events and time of day. A regime-aware σ (e.g. rolling estimate from the previous N windows) might improve model accuracy.

2. **Entry timing.** Both strategies currently allow entry anywhere in [T=10, T=290]. Empirically, entries near T=150–250 may have better risk/reward than very early entries (T=250–290) where the full random walk risk is ahead of us.

3. **Fee impact on live PnL.** The backtest does not account for taker fees (~1.5% at p=0.50). Real live results will be lower than backtest numbers. The fee structure favors entering at extreme prices (p far from 0.50), which the high min_edge filter partially achieves.

4. **Dataset breadth.** 481 windows ≈ 1.7 days. Results are directionally interesting but the sample is small. The dataset needs to be extended to include different volatility regimes (high-volatility news days, low-volatility weekends) before conclusions can be treated as robust.

5. **Liquidity.** Paper trading results from `experiments/paper/` are needed to validate that the observed prices in datasets are achievable fills — i.e., that the backtest is not simulating a price that would not have been available at size.

---

## Analysis Tools

- `testing/check_experiments/check_ticks.ipynb` — per-tick analysis of a backtest run
- `testing/check_experiments/check_trades.ipynb` — trade-level PnL and win/loss breakdown
- `testing/btc_history_analysis.ipynb` — BTC price behavior analysis across windows
- `testing/data_retriever.ipynb` — data collection and inspection utilities
