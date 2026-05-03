# Testing & Notebooks

This folder contains Jupyter notebooks for **getting started** and **analyzing experiments**.

## Getting Started

### 1. `quickstart.ipynb`

**Goal**: Run your first backtest in 30 seconds.

**What it does:**
- Sets up the environment (loads `.env`, imports modules)
- Discovers available historical datasets
- Runs a backtest on the BTC 5-minute markets
- Displays summary results

**How to use:**
1. Open in Jupyter: `jupyter notebook quickstart.ipynb`
2. Run each cell in order (Shift+Enter)
3. Observe the backtest output in the last cell

**What you'll see:**
- Win rate, total P&L, average P&L
- A short summary of the best and worst trades

---

### 2. `basic_trading.ipynb`

**Goal**: Understand how to implement a simple trading strategy.

**What it does:**
- Defines a minimal strategy (`SimpleThresholdStrategy`)
- Backtests it on a few windows
- Explains each line of the strategy code

**How to use:**
1. Open in Jupyter: `jupyter notebook basic_trading.ipynb`
2. Run cells to see the strategy execute
3. Modify the parameters (e.g., `spread_threshold=5.0`) and re-run
4. Observe how results change

**What you'll learn:**
- How `MarketState` flows into your strategy
- How `Decision` objects control trading
- How to add custom metrics for analysis

---

## Analysis Tools

### `check_experiments/check_ticks.ipynb`

**Goal**: Deep-dive into per-second tick logs from backtest runs.

**What it does:**
- Loads `ticks.csv` from recent backtest results
- Plots price evolution, actions, and P&L over time
- Identifies best and worst trades

**Use case:**
- Debugging why a strategy wins/loses
- Spotting time-of-day patterns
- Validating strategy logic

---

### `check_experiments/check_trades.ipynb`

**Goal**: Aggregate trade statistics and performance metrics.

**What it does:**
- Reads `summary.json` files
- Compares multiple backtest runs side-by-side
- Shows win rate, max drawdown, avg profit

**Use case:**
- Comparing two strategies
- Tracking improvements over time
- Validating parameter tuning

---

## Troubleshooting

### Notebook won't run / ModuleNotFoundError

**Solution**: Make sure you're in the project root:
```bash
cd /path/to/polymarket-trading-bot
jupyter notebook testing/quickstart.ipynb
```

### "No module named 'backtest'" or similar

**Solution**: Install dependencies:
```bash
uv sync
```

### Kernel keeps dying

**Solution**: Restart Jupyter entirely:
```bash
# Close Jupyter (Ctrl+C in terminal)
# Reopen:
jupyter notebook testing/quickstart.ipynb
```

---

## Next Steps

1. ✅ Run `quickstart.ipynb` — see backtest in action
2. ✅ Run `basic_trading.ipynb` — understand strategy structure
3. ✅ Modify parameters — observe impact on results
4. ✅ Create your own strategy — copy `strategies/brownian_motion.py` as a template
5. ✅ Paper trade — test against real market

For more details, see:
- [docs/strategy_guide.md](../docs/strategy_guide.md) — full strategy API
- [README.md](../README.md) — project overview
- [.claude/CLAUDE.md](../.claude/CLAUDE.md) — technical deep-dive
