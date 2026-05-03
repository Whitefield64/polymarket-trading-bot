# Polymarket BTC Trading Bot

A Python algorithmic trading bot for **BTC 5-minute Up/Down binary markets** on Polymarket. Targets prediction of BTC price direction within a 5-minute window using **Brownian Motion volatility estimation**.

**Key capabilities:**
- 📊 **Backtest** on 635+ historical 5-minute windows (fast iteration)
- 📄 **Paper trading** against real market (no money at risk)
- 🚀 **Live trading** (toggle single env flag: `PAPER_MODE=false`)
- All three modes use **identical strategy code** — no duplicated logic

**Current performance** (Brownian Motion strategy):
- **Win rate**: 80% across 50 trades (backtest)
- **Total P&L**: +$232 USD (on $3 position size)
- **Max drawdown**: 8.6% (very conservative)

---

## The Problem & Why Brownian Motion?

### The Challenge
Predict whether BTC will go up or down in exactly 5 minutes on Polymarket. Binary markets: either you're right (2x return), or you lose your stake.

### First Attempt: Neural Networks
We tried **LSTM recurrent networks** trained on historical price data:
- ❌ Heavy overfitting on training windows
- ❌ Latency: inference took 100-500ms per tick (too slow for 5m windows)
- ❌ Bias toward recent data caused false signals
- ❌ Required massive retraining when market conditions shifted

**Lesson**: Complex models fit noise, not signal. The problem is smaller than we thought.

### The Solution: Brownian Motion + Sigma Estimation
**Core insight**: BTC price changes follow a random walk. We don't need to predict the direction — we need to estimate the *probability edge* between what the market thinks and what the math says.

**Why this works:**
1. **Statistically sound** — Brownian Motion is the standard model for financial time-series
2. **No training data needed** — calculate volatility (sigma) from the previous 5-min window's price history
3. **Fast** — sigma calculation = 2-3 milliseconds
4. **Robust** — works in uptrends, downtrends, and ranging markets
5. **Interpretable** — you understand exactly why each trade fired

---

## How It Works

### The Algorithm (High-Level)

```
1. Window starts (e.g., at 2026-05-03 14:00:00 UTC)
   ↓
2. Extract target BTC price (locked at window open)
   ↓
3. Every second, observe live BTC price via Chainlink WebSocket
   ↓
4. Calculate spread = live_price - target_price
   ↓
5. Estimate volatility (sigma) from previous 100+ BTC candles
   ↓
6. Apply Brownian Motion formula with tau=300s (window duration)
   ↓
7. Compute P(UP) = probability BTC goes up in remaining time
   ↓
8. Compare P(UP) vs market price (odds of UP token)
   ↓
9. If edge > threshold (default 0.5%) AND no position → trade
   ↓
10. If position in profit > threshold → sell (take profit)
   ↓
11. Repeat every second until window closes
```

### The Math

**Brownian Motion predicts price direction based on volatility:**

```
P(UP) = Φ( spread / (sigma * sqrt(tau)) )
```

Where:
- `spread` = current price delta from window open
- `sigma` = annualized volatility (estimated from historical data)
- `tau` = time remaining in seconds (300 → 0)
- `Φ()` = cumulative normal distribution

**Edge calculation:**
```
edge = abs( P(UP) - market_price ) × size
```

**Trade only if:**
```
edge > min_edge_threshold   (default: 0.5% edge per $3 position)
```

### Why It Wins

1. **Market is inefficient** — Mid-price lags slightly; we spot it first
2. **Volatility is persistent** — Sigma from previous hour is good predictor for next 5 min
3. **Timing advantage** — We calculate edge every second; market prices update every 0.1–1 second
4. **Risk control** — Position size scales with confidence (edge)

---

## Running the Bot

### Prerequisites

```bash
# Python 3.11+, uv package manager
git clone <this-repo>
cd polymarket-trading-bot
uv sync

# Copy and fill .env (see .env.example for details)
cp .env.example .env
```

**Required credentials in `.env`:**
```
POLY_PRIVATE_KEY=0xYourMetaMaskPrivateKey
POLY_SAFE_ADDRESS=0xYourPolymarketSafeAddress
PAPER_MODE=true          # Set to false for live trading
```

### Backtest (Fast)

Test the strategy on historical data — runs 635+ windows in seconds:

```bash
# Run all historical windows
uv run python run_backtest.py --strategy brownian_motion

# Quiet mode (summary only)
uv run python run_backtest.py --strategy brownian_motion --quiet

# Specific date range (Unix timestamps)
uv run python run_backtest.py --strategy brownian_motion --start 1774979400 --end 1775016900
```

**Output** → `experiments/BrownianStrategy_{timestamp}/`:
- `ticks.csv` — one row per second (spread, prices, actions, P&L)
- `summary.json` — aggregate stats (win rate, total P&L, max drawdown)

### Paper Trading (Real Market, No Money)

Trade against live Polymarket prices without risking real funds:

```bash
uv run python run_paper.py --strategy brownian_motion
```

**Output** → `experiments/paper/` with live results

### Live Trading (Real Money)

⚠️ **Prerequisites:** Polymarket account with USDC balance, Builder Program credentials (for gasless).

```bash
# In .env, set PAPER_MODE=false
PAPER_MODE=false

# Run
uv run python run_paper.py --strategy brownian_motion
```

**First run shows a confirmation prompt.** Strategy code is **identical to backtest** — only execution mode changes.

---

## Results & Backtesting

See [experiments/README.md](experiments/README.md) for how to interpret backtest outputs.

### Recent Runs

| Strategy | Mode | Win Rate | Avg P&L | Total P&L | Trades |
|----------|------|----------|---------|-----------|--------|
| Brownian Motion | Backtest | 71% | $4.49 | $219.83 | 49 |
| New Strategy | Backtest | 80% | $4.64 | $232.08 | 50 |

Both strategies share the same core Brownian Motion engine with different parameter tuning.

---

## Architecture

### Three-Mode Design

```
┌──────────────────────────────────────────────────────────┐
│                     Strategy Code                        │
│              (strategies/brownian_motion.py)             │
│         Returns: Decision(action, reasoning)             │
└─────────────┬──────────────────────────────────────────┘
              │
    ┌─────────┴──────────┬──────────────┐
    │                    │              │
    ▼                    ▼              ▼
┌─────────┐        ┌──────────┐    ┌──────────┐
│ Backtest│        │  Paper   │    │  Live    │
│ Engine  │        │ Trading  │    │ Trading  │
│(CSV →   │        │(WebSocket│    │(WebSocket│
│PnL)     │        │→ Virtual)│    │→ Real $) │
└────┬────┘        └─────┬────┘    └────┬─────┘
     │                   │              │
     ▼                   ▼              ▼
  CSV Output      Virtual Positions  Real Orders
 (ticks.csv,     (paper mode)        (signed via
  summary.json)                       EIP-712)
```

### Key Modules

| Module | Purpose |
|--------|---------|
| `strategies/base.py` | `MarketState`, `Decision`, `Action` interface |
| `strategies/brownian_motion.py` | Main trading logic (sigma calc, edge detection) |
| `backtest/engine.py` | CSV loop, position simulation |
| `trader/market_feed.py` | WebSocket → `MarketState` tick stream |
| `src/bot.py` | Order placement and cancellation |
| `lib/position_manager.py` | P&L tracking, TP/SL helpers |

### Strategy Interface

All strategies (backtest, paper, live) implement the same interface:

```python
class MyStrategy(BaseStrategy):
    def on_tick(self, state: MarketState) -> Decision:
        """
        Called every second. Return a trading decision.
        
        Args:
            state: Market snapshot (price, spread, position status)
            
        Returns:
            Decision(action=..., reasoning=..., metrics={...})
        """
        # Your logic here
        return Decision(Action.HOLD, "waiting", {})
    
    def on_start(self, window_id: str):
        """Optional: called at window open"""
        pass
    
    def on_end(self, window_id: str, outcome: str):
        """Optional: called at window close (outcome='up' or 'down')"""
        pass
```

Read [docs/strategy_guide.md](docs/strategy_guide.md) to implement your own.

---

## Getting Started

### 1. First Backtest (30 seconds)

```bash
uv run python run_backtest.py --strategy brownian_motion --quiet
```

You'll see a summary of wins/losses and P&L.

### 2. Understand Results

See `experiments/{strategy}_{timestamp}/summary.json`:
- `win_rate` — % of winning trades
- `total_pnl` — total dollars gained/lost
- `avg_pnl` — per-trade average
- `max_drawdown` — worst peak-to-trough loss

See `ticks.csv` for per-second granularity (debugging).

### 3. Paper Trade (Optional)

```bash
uv run python run_paper.py --strategy brownian_motion
```

Logs output to terminal; results saved to `experiments/`.

### 4. Modify Parameters

Edit `strategies/brownian_motion.py`:
- `min_edge` — minimum edge to trigger a trade (default 0.5%)
- `profit_take` — TP threshold (default: close at window end)
- `min_time_left` / `max_time_left` — window timing constraints

Re-backtest to validate.

### 5. Write Your Own Strategy

Copy `strategies/brownian_motion.py` to `strategies/my_strategy.py`, implement `on_tick()`, and run:

```bash
uv run python run_backtest.py --strategy my_strategy
```

See [docs/strategy_guide.md](docs/strategy_guide.md) for a template.

---

## Configuration

### Environment Variables

See `.env.example`:

```
# Required
POLY_PRIVATE_KEY=0x...
POLY_SAFE_ADDRESS=0x...

# Trading
PAPER_MODE=true              # false for live trading
TRADE_SIZE=3.0              # USDC per position

# Gasless (optional)
POLY_BUILDER_API_KEY=...
POLY_BUILDER_API_SECRET=...
POLY_BUILDER_API_PASSPHRASE=...
```

### Config File (Optional)

Alternatively, load from `config.yaml`:

```yaml
poly:
  safe_address: "0x..."
  builder_api_key: "..."
trader:
  paper_mode: true
  trade_size: 3.0
```

---

## Technical Stack

- **Python 3.11** — Core language
- **uv** — Fast package manager
- **async/await** — Real-time WebSocket handling
- **Polymarket CLOB API** — Order submission
- **EIP-712 signing** — Secure order authentication (Gnosis Safe)
- **WebSocket** — Live price & orderbook streams
- **Pandas** — CSV data handling (backtest)

---

## For Developers

### Files to Study

1. **`strategies/brownian_motion.py`** — Main strategy (80% win rate)
2. **`backtest/engine.py`** — How backtesting works (CSV loop)
3. **`trader/market_feed.py`** — How live data flows (WebSocket → MarketState)
4. **`src/bot.py`** — Order lifecycle (signing, submission, cancellation)

### Architecture Deep-Dive

See [.claude/CLAUDE.md](.claude/CLAUDE.md) for:
- Complete module breakdown
- API reference
- Polymarket integration details
- Advanced configuration

---

## Support

- **Questions on strategy development?** See [docs/strategy_guide.md](docs/strategy_guide.md)
- **Technical deep-dive?** See [.claude/CLAUDE.md](.claude/CLAUDE.md)
- **Results interpretation?** See [experiments/README.md](experiments/README.md)
- **Polymarket API docs?** See [docs/developers/](docs/developers/)

---

## License

See [LICENSE](LICENSE).

---

## Notes

- This bot was originally developed to explore **Brownian Motion as an alternative to neural networks** for financial prediction. The key finding: simple, interpretable models often outperform complex ones in low-signal domains.
- All code is production-ready but provided **as-is** without warranty. Always start with paper trading before going live.
- The Polymarket market is real, 24/7, and global — your edge can disappear fast. Monitor live results closely.
