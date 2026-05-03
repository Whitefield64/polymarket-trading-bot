"""
Base strategy interface.

All strategies must subclass BaseStrategy and implement on_tick().
The same strategy class is used in backtesting, paper trading, and live trading —
the engine handles the difference, not the strategy.

Data flow:
  engine builds MarketState → calls strategy.on_tick() → gets Decision
  Decision.action  — what to do (BUY_UP / BUY_DOWN / CLOSE / HOLD)
  Decision.reasoning — mandatory string explaining why (logged per second)
  Decision.metrics   — optional dict of custom values (become columns in ticks.csv)
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Action(str, Enum):
    BUY_UP   = "BUY_UP"
    BUY_DOWN = "BUY_DOWN"
    CLOSE    = "CLOSE"    # exit current position early
    HOLD     = "HOLD"     # do nothing


@dataclass
class MarketState:
    """
    One tick of market data. Identical shape in backtest and live trading.

    In backtest:     populated from a CSV row.
    In live trading: populated from WebSocket snapshots + Vatic API target.

    CSV columns map exactly to the first six fields.
    """
    # Core market data — matches CSV columns
    time_left:   int    # seconds remaining in the 5-minute window (300 → 0)
    target_btc:  float  # target BTC price locked at window start (from Vatic API)
    live_btc:    float  # current real BTC price
    spread:      float  # live_btc - target_btc (positive = BTC rose above target)
    up_price:    float  # UP outcome probability price [0.0, 1.0]
    down_price:  float  # DOWN outcome probability price [0.0, 1.0]

    # Position context — injected by engine before calling strategy
    has_position:    bool          = False
    position_side:   Optional[str] = None    # "up" or "down"
    position_entry:  float         = 0.0     # price when position was opened
    position_pnl:    float         = 0.0     # unrealized PnL at current prices

    # Window metadata
    window_id: str = ""  # e.g. "btc-updown-5m-1774979400"


@dataclass
class Decision:
    """
    What the strategy wants to do this tick.

    reasoning is mandatory — it is logged every second in backtest output
    and displayed in paper mode. Write it as a human-readable explanation:
    e.g. "spread=+8.3 > threshold=5.0 → BUY_UP"

    metrics is a free-form dict. Any keys added here automatically become
    extra columns in the backtest ticks.csv output. Use it for any computed
    values you want to inspect later (rolling means, z-scores, model outputs…).
    """
    action:    Action
    reasoning: str
    metrics:   dict[str, Any]   = field(default_factory=dict)
    price:     Optional[float]  = None   # None = use current mid-price
    size:      float            = 0.0    # USDC; 0 = use default from config


class BaseStrategy:
    """
    Minimal base class for all trading strategies.

    Override on_tick(). The other hooks are optional.

    Rules:
    - on_tick() must be synchronous (no async/await, no blocking I/O)
    - Do not import from backtest/ or trader/ — those layers call you, not the reverse
    - Keep all state on self (the engine creates one instance per run)
    """

    def on_start(self, window_id: str) -> None:
        """Called once at the beginning of each 5-minute window."""
        pass

    def on_tick(self, state: MarketState) -> Decision:
        """
        Called once per second (backtest) or per WebSocket update (live).
        Must return a Decision — never return None.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement on_tick()"
        )

    def tick(self, state: MarketState) -> Decision:
        """Engine-facing entry point. Guards against zero BTC price."""
        if state.live_btc == 0:
            return Decision(Action.HOLD, "live_btc=0 (price not yet available)")
        if state.target_btc == 0:
            return Decision(Action.HOLD, "target_btc=0 (initial price not available)")
        if state.up_price == 0 or state.down_price == 0:
            return Decision(Action.HOLD, f"up_price={state.up_price}, down_price={state.down_price} (incomplete market data)")
        return self.on_tick(state)

    def on_end(self, window_id: str, outcome: str) -> None:
        """
        Called at the end of each window with the actual outcome.

        outcome: "up" if BTC closed above target, "down" otherwise.
        Useful for updating internal models or logging performance.
        """
        pass

    def get_params(self) -> dict[str, Any]:
        """Return strategy config as a dict for summary logging.

        Looks for a `cfg` attribute that is a dataclass (e.g. ThresholdConfig).
        Override this if your strategy stores params differently.
        """
        cfg = getattr(self, "cfg", None)
        if cfg is not None and dataclasses.is_dataclass(cfg):
            return dataclasses.asdict(cfg)
        return {}
