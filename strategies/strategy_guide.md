# Strategy Development Guide

This guide explains how to build a new trading strategy on top of this repo. Strategies work seamlessly across **backtest**, **paper trading**, and **live trading** modes.

## 1) Architecture Overview

The bot supports three execution modes using the same strategy code:

| Mode | Use Case | Speed | Data Source |
|------|----------|-------|-------------|
| **Backtest** | Historical testing, fast iteration | Seconds per 5-min window | CSV files |
| **Paper Trading** | Live market, no real money | Real-time | WebSocket feed |
| **Live Trading** | Real money (toggle `PAPER_MODE=false`) | Real-time | WebSocket feed |

Strategy interface:
- **Input**: `MarketState` — market snapshot (price, spread, mid-prices, position tracking)
- **Output**: `Decision` — action (BUY_UP, BUY_DOWN, HOLD, SELL) + reasoning
- **Lifecycle**: `on_tick()` called every second (backtest) or per update (live)

---

## 2) Core Components

### Base Strategy Interface (`strategies/base.py`)

```python
from enum import Enum
from dataclasses import dataclass

class Action(Enum):
    """Trading actions available to strategies"""
    BUY_UP = "buy_up"
    BUY_DOWN = "buy_down"
    SELL = "sell"
    HOLD = "hold"

@dataclass
class MarketState:
    """Current market snapshot"""
    time_left: int          # Seconds remaining in 5-min window (300 → 0)
    target_btc: float       # Target BTC price locked at window start
    live_btc: float         # Current real BTC price
    spread: float           # live_btc - target_btc
    up_price: float         # UP token mid-price [0, 1]
    down_price: float       # DOWN token mid-price [0, 1]
    has_position: bool      # Currently holding a position
    position_side: str      # "up" or "down" if has_position
    position_entry: float   # Entry price of current position
    position_pnl: float     # Unrealized P&L
    window_id: str          # e.g. "btc-updown-5m-1774979400"

@dataclass
class Decision:
    """Strategy decision at each tick"""
    action: Action
    reasoning: str          # Always mandatory — appears in logs
    metrics: Dict[str, float] = None  # Custom metrics for analysis
```

### Other Reusable Components

- `lib/position_manager.py` — Position tracking with TP/SL helpers
- `lib/console.py` — Structured logging utilities
- `backtest/simulator.py` — Binary market math (used automatically in backtest)
- `src/bot.py` — Order placement (used automatically in live/paper modes)

---

## 3) Minimum Setup

### Environment Variables

For **paper/live trading** (backtest needs none):

```bash
export POLY_PRIVATE_KEY=0xYourPrivateKey
export POLY_SAFE_ADDRESS=0xYourSafeAddress
export PAPER_MODE=true  # Set to false for live trading
```

Optional (gasless mode):

```bash
export POLY_BUILDER_API_KEY=...
export POLY_BUILDER_API_SECRET=...
export POLY_BUILDER_API_PASSPHRASE=...
```

### Quick Test

```bash
# Backtest your strategy
uv run python run_backtest.py --strategy brownian_motion --quiet

# Paper trading (live market, virtual positions)
uv run python run_paper.py --strategy brownian_motion
```

---

## 4) Strategy Template

Create `strategies/my_strategy.py`:

```python
from strategies.base import Action, BaseStrategy, Decision, MarketState


class MyStrategy(BaseStrategy):
    """Simple threshold-based strategy for portfolio"""
    
    def on_tick(self, state: MarketState) -> Decision:
        """
        Called every second (backtest) or per WebSocket update (live).
        
        Args:
            state: Current market snapshot
            
        Returns:
            Decision with action, reasoning, and optional metrics
        """
        metrics = {
            "spread": state.spread,
            "up_price": state.up_price,
            "down_price": state.down_price,
        }
        
        # Example: buy UP if spread > 5 and no position
        if state.spread > 5.0 and not state.has_position:
            return Decision(
                action=Action.BUY_UP,
                reasoning=f"spread={state.spread:.2f} > 5.0 threshold",
                metrics=metrics
            )
        
        # Example: sell if position is in profit
        if state.has_position and state.position_pnl > 10.0:
            return Decision(
                action=Action.SELL,
                reasoning=f"position_pnl={state.position_pnl:.2f} — take profit",
                metrics=metrics
            )
        
        return Decision(
            action=Action.HOLD,
            reasoning="waiting for signal",
            metrics=metrics
        )
    
    def on_start(self, window_id: str):
        """Optional: called at the start of each 5-min window"""
        pass
    
    def on_end(self, window_id: str, outcome: str):
        """Optional: called at the end of each window (outcome = 'up' or 'down')"""
        pass


# Required for auto-discovery by run_backtest.py and run_paper.py
STRATEGY_CLASS = MyStrategy
```

---

## 5) Key Rules

1. **`on_tick()` must be synchronous** — no async/await, no blocking I/O
2. **`Decision.reasoning` is mandatory** — appears in every tick log
3. **`Decision.metrics` dict** — becomes extra columns in `ticks.csv` during backtest
4. **Do NOT import** `backtest/` or `trader/` modules inside your strategy — the engine handles execution
5. **MarketState fields are read-only** — compute your own derived metrics
6. **Position management** — use `state.has_position`, `state.position_side`, `state.position_pnl` to track status

---

## 6) Running Your Strategy

### Backtest

```bash
# Run over all historical windows
uv run python run_backtest.py --strategy my_strategy

# Quiet mode (summary only)
uv run python run_backtest.py --strategy my_strategy --quiet

# Specific date range
uv run python run_backtest.py --strategy my_strategy --start 1774979400 --end 1775016900
```

Output → `experiments/MyStrategy_{timestamp}/`:
- `ticks.csv` — one row per second with all metrics + actions
- `summary.json` — aggregate stats (win rate, total PnL, max drawdown)

### Paper / Live Trading

```bash
# Paper trading (PAPER_MODE=true in .env)
uv run python run_paper.py --strategy my_strategy

# Live trading (set PAPER_MODE=false first)
uv run python run_paper.py --strategy my_strategy
```

Output → `experiments/` folder with timestamp-named results.

---

## 7) Workflow: From Idea to Portfolio-Ready

1. **Draft** your idea in `strategies/my_strategy.py`
2. **Backtest** to validate on historical data
3. **Inspect results** using `ticks.csv` + `summary.json`
4. **Paper trade** for real-time validation (no money at risk)
5. **Polish** based on observed edge
6. **Deploy** to live (set `PAPER_MODE=false`) or keep as portfolio piece

---

## 8) Example: Brownian Motion Strategy

See `strategies/brownian_motion.py` for the current production strategy:

- Uses historical BTC volatility (sigma) to estimate edge
- Compares calculated probabilities vs market prices
- Trades only when edge exceeds configured threshold
- Includes position management (TP/SL)

Read the source and [README.md](../README.md) for the mathematical foundation.

## 7) Trading Helpers

You can place orders two ways:

1) Convenience helpers:
- `await self.execute_buy(side, price)`
- `await self.execute_sell(position, price)`

2) Direct bot calls:

```python
await self.bot.place_order(token_id, price, size, side="BUY")
```

If you use direct calls, you should also update `PositionManager` yourself.

## 8) Risk Controls

Recommended defaults in config:

- `max_positions`: limit exposure
- `take_profit`: auto exit when up X dollars
- `stop_loss`: auto exit when down X dollars

Example:

```python
MyStrategyConfig(
    max_positions=1,
    take_profit=0.10,
    stop_loss=0.05,
)
```

## 9) Common Pitfalls

- **Async blocking**: use the existing async API; do not call `requests` directly
  inside `on_tick`.
- **Token ID mixups**: use `self.token_ids["up"]` / `self.token_ids["down"]`.
- **Position sizing**: `execute_buy()` uses `config.size` as USDC size, then
  converts to shares by `size / price`.
- **No data yet**: on startup, prices can be `0`. Guard your logic.

## 10) Testing Tips

Unit test signal logic with small inputs. Mock bot calls:

```python
import pytest
from unittest.mock import AsyncMock

@pytest.mark.asyncio
async def test_entry_signal(monkeypatch):
    bot = AsyncMock()
    strategy = MyStrategy(bot=bot, config=MyStrategyConfig(entry_price=0.5))
    await strategy.on_tick({"up": 0.45})
    assert bot.place_order.called
```

## 11) Debug Checklist

- Run `python apps/orderbook_tui.py --coin ETH` to confirm data flow.
- Log `prices` in `on_tick()` to ensure you see updates.
- Check your Safe address and environment variables.

---

If you want, you can copy `strategies/flash_crash.py` and start from there.
