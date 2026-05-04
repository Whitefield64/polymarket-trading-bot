# Polymarket Trading Bot

Automated strategy research platform for Polymarket's BTC 5-minute binary prediction markets. Strategies are backtested against 481 historical 5-minute windows, then promoted to paper or live trading with an identical interface — the same strategy class runs in all three modes.

---

## What Are These Markets?

Each market asks: **will BTC be higher or lower at the end of this 5-minute window than at its start?** Markets run 24/7, aligned to Unix epoch boundaries divisible by 300 seconds (288 markets/day for BTC alone). Prices are probabilities in [0, 1]. The oracle is Chainlink; settlement is on-chain within ~2 minutes.

---

## Architecture

```
run_backtest.py   — offline simulation over historical CSVs
run_paper.py      — live feed, paper or real orders (PAPER_MODE in .env)

strategies/       — one file per strategy; same class in all modes
backtest/         — engine, CSV loader, simulator, report
trader/           — async WebSocket engine for live/paper trading
src/              — Polymarket API client (orders, CLOB, signer)
datasets/         — 481 historical 5-min windows (CSV)
experiments/      — run output: ticks.csv + summary.json per run
data_extract/     — data collection scripts
testing/          — analysis notebooks
```

---

## Quick Start

```bash
# Install
uv sync

# Backtest a strategy
uv run python run_backtest.py --strategy brownian_motion
uv run python run_backtest.py --strategy brownian_motion --size 10 --quiet

# Paper trade (live market, no real money)
uv run python run_paper.py --strategy brownian_motion
uv run python run_paper.py --strategy brownian_motion --continuous

# Run tests
uv run pytest
```

Copy `.env.example` to `.env` and fill in API credentials for live trading. Set `PAPER_MODE=false` to place real orders (requires a typed confirmation).

---

## Strategies

| Strategy | File | Description |
|---|---|---|
| `brownian_motion` | `strategies/brownian_motion.py` | Brownian model edge, optional profit-take exit |
| `brownian_edge_exit` | `strategies/brownian_edge_exit.py` | Same model, exits when edge turns negative |
| `example_threshold` | `strategies/example_threshold.py` | Simple spread-threshold reference implementation |

Every strategy is a Python class implementing a single `on_tick(state: MarketState) -> Decision` method. To write your own, see the [Strategy Guide](strategies/strategy_guide.md).

---

## Selected Results

Backtested across 481 windows, $3 USDC per trade:

| Strategy | Trades | Win Rate | Total PnL | Avg PnL/Trade | Max Drawdown |
|---|---|---|---|---|---|
| `BrownianStrategy` | 30 | 50.0% | +$71.17 | +$2.37 | $18.00 |
| `EdgeExitStrategy` | 31 | 58.1% | +$89.26 | +$2.88 | $8.60 |

The edge-exit variant exits early when the model's advantage over the market price disappears. This tightens drawdown significantly (+119% drawdown reduction) while improving win rate and average PnL. See [Experiments](EXPERIMENTS.md) for the full analysis.

> The strategies published here are the baseline research implementations used as reference points. Subsequent iterations with improved entry filters, dynamic σ, and position sizing are not included in this repository.

---

## Stack

- **Python 3.11** + `uv` for dependency management
- **Polymarket CLOB API** — REST + WebSocket order book
- **Chainlink** oracle for BTC strike price and settlement
- **Vatic Trading API** for the reference (strike) price
- **EIP-712 / Gnosis Safe** for on-chain order signing
- **Polygon** network for settlement
- `scipy` / `math` for the Brownian motion model
- `pytest` for unit tests

---

## Documentation

- [Strategy Guide](strategies/strategy_guide.md) — interface, hooks, config, testing pipeline
- [Market Mechanics](.claude/btc5m_market_mechanics.md) — oracle, CLOB, price dynamics, fees, settlement
- [Experiments & Research Log](EXPERIMENTS.md) — model derivation, strategy evolution, results analysis
