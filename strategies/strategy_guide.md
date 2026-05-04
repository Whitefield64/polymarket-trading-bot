# Strategy Guide

How to implement, configure, and test a trading strategy in this framework.

---

## 1. The interface

Every strategy is a Python class that inherits `BaseStrategy` and implements `on_tick()`. The same class runs unchanged in backtest, paper, and live mode.

```python
from strategies.base import BaseStrategy, MarketState, Decision, Action

class MyStrategy(BaseStrategy):
    def on_tick(self, state: MarketState) -> Decision:
        ...

STRATEGY_CLASS = MyStrategy  # required at the bottom of the file
```

---

## 2. MarketState

`on_tick()` receives a `MarketState` on every tick (once per second in backtest; per WebSocket update in live). All fields are read-only.

```python
@dataclass
class MarketState:
    # Core market data
    time_left:    int    # seconds remaining in the window (counts 300 → 0)
    target_btc:   float  # strike price locked at window open (from Vatic API)
    live_btc:     float  # current BTC price (from Chainlink WebSocket)
    spread:       float  # live_btc - target_btc (positive = BTC above strike)
    up_price:     float  # UP token market price — the market's P(UP) ∈ [0, 1]
    down_price:   float  # DOWN token market price — the market's P(DOWN) ∈ [0, 1]

    # Position context (injected by the engine)
    has_position:   bool  = False
    position_side:  str   = None    # "up" or "down"
    position_entry: float = 0.0     # token price at entry
    position_pnl:   float = 0.0     # unrealized PnL at current prices

    window_id: str = ""   # e.g. "btc-updown-5m-1774979400"
```

**Useful invariants:**
- `spread > 0` means BTC is currently above the strike → UP is more likely
- `up_price + down_price` ≈ 1.0 (small difference due to fees)
- At `time_left == 0`, any open position is settled by the engine at the final spread
- The engine will call `HOLD` automatically if `live_btc`, `target_btc`, `up_price`, or `down_price` are zero

---

## 3. Decision

`on_tick()` must always return a `Decision`. Never return `None`.

```python
@dataclass
class Decision:
    action:    Action          # what to do this tick
    reasoning: str             # mandatory — logged every second in ticks.csv
    metrics:   dict = {}       # optional — extra columns in ticks.csv
    price:     float = None    # None = use current mid-price
    size:      float = 0.0     # USDC; 0 = use default from config
```

`reasoning` is logged every second. Write it as a human-readable string so you can understand each tick when debugging:
```python
f"edge_up={edge_up:+.4f} > min_edge={self.cfg.min_edge} → BUY_UP"
```

`metrics` is a free-form dict. Any key you add here becomes an extra column in `ticks.csv`. Use it for any computed value you want to inspect:
```python
metrics = {"p_up": 0.67, "sigma_tau": 12.3, "z_score": 1.4}
```

---

## 4. Action

```python
class Action(str, Enum):
    BUY_UP   = "BUY_UP"    # open a long-UP position
    BUY_DOWN = "BUY_DOWN"  # open a long-DOWN position
    CLOSE    = "CLOSE"     # close the current position early
    HOLD     = "HOLD"      # do nothing
```

**Engine rules:**
- You can hold at most one position per window
- If you return `BUY_UP`/`BUY_DOWN` while already in a position, it is ignored
- `CLOSE` while not in a position is also ignored
- Positions not closed manually are settled by the engine at window end

---

## 5. Optional hooks

```python
def on_start(self, window_id: str) -> None:
    """Called once at the beginning of each 5-minute window."""
    pass

def on_end(self, window_id: str, outcome: str) -> None:
    """Called at window close with the actual result. outcome = "up" or "down"."""
    pass
```

Use `on_start` to reset per-window accumulators (e.g. rolling mean, trade count).
Use `on_end` to log win/loss streaks or adapt internal parameters across windows.

---

## 6. Configuration pattern

Store strategy parameters in a dataclass and assign it to `self.cfg`. The base class picks it up automatically and saves it to `summary.json`.

```python
from dataclasses import dataclass

@dataclass
class MyConfig:
    threshold:     float = 0.05
    min_time_left: int   = 30
    max_time_left: int   = 290

class MyStrategy(BaseStrategy):
    def __init__(self, config: MyConfig = MyConfig()):
        self.cfg = config
```

---

## 7. Rules

- `on_tick()` must be **synchronous** — no `async`, no blocking I/O, no network calls
- Do not import from `backtest/` or `trader/` — those layers call you, not the reverse
- Keep all mutable state on `self` — the engine creates one instance per run and reuses it across windows

---

## 8. Minimal working example

```python
from dataclasses import dataclass
from strategies.base import Action, BaseStrategy, Decision, MarketState

@dataclass
class ThresholdConfig:
    spread_threshold: float = 10.0  # enter when |spread| exceeds this (USD)
    min_time_left:    int   = 30

class ThresholdStrategy(BaseStrategy):
    def __init__(self, config: ThresholdConfig = ThresholdConfig()):
        self.cfg = config

    def on_tick(self, state: MarketState) -> Decision:
        t  = state.time_left
        sp = state.spread

        # Exit: hold until settlement (no early exit logic)
        if state.has_position:
            return Decision(
                Action.HOLD,
                f"holding {state.position_side} | pnl={state.position_pnl:+.4f}",
            )

        # Time guard
        if t < self.cfg.min_time_left:
            return Decision(Action.HOLD, f"t={t} < min_time_left={self.cfg.min_time_left}")

        # Entry
        if sp > self.cfg.spread_threshold:
            return Decision(
                Action.BUY_UP,
                f"spread={sp:+.2f} > {self.cfg.spread_threshold} → BUY_UP",
            )
        if sp < -self.cfg.spread_threshold:
            return Decision(
                Action.BUY_DOWN,
                f"spread={sp:+.2f} < -{self.cfg.spread_threshold} → BUY_DOWN",
            )

        return Decision(Action.HOLD, f"spread={sp:+.2f} inside band ±{self.cfg.spread_threshold}")

STRATEGY_CLASS = ThresholdStrategy
```

See `strategies/example_threshold.py` for the full version. See `strategies/brownian_motion.py` for a production-quality example with profit-take, edge-gone exit, and per-tick metrics.

---

## 9. Testing pipeline


### Step 1 — Backtest on historical data

```bash
uv run python run_backtest.py --strategy my_strategy
```

Runs your strategy on all 635+ CSVs in `datasets/`. Results are saved to `experiments/backtest/MyStrategy_{timestamp}/`:
- `ticks.csv` — one row per second: full MarketState + action + reasoning + metrics
- `summary.json` — aggregate stats: win_rate, total_pnl, avg_pnl, max_drawdown, num_trades, params

Useful flags:
```bash
--quiet              # print only the final summary, not per-window logs
--size 5.0           # USDC per trade (default: 1.0)
--start 1774979400   # backtest from this Unix timestamp
--end   1775016900   # backtest up to this Unix timestamp
```

### Step 2 — Paper trade against live prices

```bash
uv run python run_paper.py --strategy my_strategy
```

Connects to Polymarket via WebSocket. Your strategy receives live `MarketState` ticks — identical interface to backtest. No money is spent. Results saved to `experiments/paper/`.

Add `--continuous` to run across multiple windows without stopping:
```bash
uv run python run_paper.py --strategy my_strategy --continuous
```

### Step 3 — Live trading

Set `PAPER_MODE=false` in `.env`. Run `run_paper.py` again. A confirmation prompt appears before any real orders are placed.

**Always validate in backtest and paper before going live.** The strategy code is identical across all three modes — only the execution layer changes.

---

## 10. Reading backtest output

Key fields in `summary.json`:

```json
{
  "strategy": "ThresholdStrategy",
  "win_rate": 0.72,
  "total_pnl": 18.4,
  "avg_pnl": 1.84,
  "max_drawdown": -3.2,
  "num_trades": 10,
  "params": {"spread_threshold": 10.0, "min_time_left": 30}
}
```

In `ticks.csv`, filter rows where `action != "HOLD"` to see only trade events. Any keys you added to `Decision.metrics` appear as extra columns.

See [EXPERIMENTS.md](../EXPERIMENTS.md) for results analysis and strategy comparisons.
