"""
Example strategy: simple spread threshold.

Enters a position when |spread| (live_btc - target_btc) crosses a threshold,
betting that the current direction will persist to settlement.
Exits early if the spread reverses past a close threshold.

This is intentionally simple — it exists as a reference implementation
showing how to structure on_tick(), compute metrics, and write reasoning strings.

Usage:
    uv run python run_backtest.py --strategy example_threshold
    uv run python run_paper.py --strategy example_threshold
"""

from dataclasses import dataclass

from strategies.base import Action, BaseStrategy, Decision, MarketState


@dataclass
class ThresholdConfig:
    spread_entry:  float = 5.0    # enter when |spread| > this (USD)
    spread_close:  float = 0.0    # exit when spread crosses zero (reversal)
    min_time_left: int   = 30     # don't enter in the last N seconds
    max_time_left: int   = 280    # don't enter in the first few seconds (prices unstable)


class ThresholdStrategy(BaseStrategy):
    """
    Spread-threshold strategy.

    Logic:
      - Entry: |spread| > spread_entry AND time window is open
        - spread > 0 → BUY_UP  (BTC above target, bet it stays up)
        - spread < 0 → BUY_DOWN (BTC below target, bet it stays down)
      - Exit: spread crosses zero against our position
      - Settle: if no explicit exit, position closes at window end
    """

    def __init__(self, config: ThresholdConfig = ThresholdConfig()):
        self.cfg = config

    def on_tick(self, state: MarketState) -> Decision:
        sp = state.spread
        t  = state.time_left

        # Custom metrics — appear as columns in ticks.csv
        metrics = {
            "abs_spread":  round(abs(sp), 4),
            "in_window":   int(self.cfg.min_time_left <= t <= self.cfg.max_time_left),
        }

        # ── Exit logic (checked before entry) ──────────────────────────────
        if state.has_position:
            side = state.position_side
            if side == "up" and sp <= self.cfg.spread_close:
                return Decision(
                    Action.CLOSE,
                    f"spread={sp:+.2f} reversed against UP (close threshold={self.cfg.spread_close})",
                    metrics,
                )
            if side == "down" and sp >= -self.cfg.spread_close:
                return Decision(
                    Action.CLOSE,
                    f"spread={sp:+.2f} reversed against DOWN (close threshold={self.cfg.spread_close})",
                    metrics,
                )
            return Decision(
                Action.HOLD,
                f"holding {side} | spread={sp:+.2f} | pnl={state.position_pnl:+.4f}",
                metrics,
            )

        # ── Entry logic ────────────────────────────────────────────────────
        if not metrics["in_window"]:
            return Decision(
                Action.HOLD,
                f"t={t} outside entry window [{self.cfg.min_time_left}, {self.cfg.max_time_left}]",
                metrics,
            )

        if sp > self.cfg.spread_entry:
            return Decision(
                Action.BUY_UP,
                f"spread={sp:+.2f} > threshold={self.cfg.spread_entry} → BUY_UP",
                metrics,
            )

        if sp < -self.cfg.spread_entry:
            return Decision(
                Action.BUY_DOWN,
                f"spread={sp:+.2f} < -threshold={self.cfg.spread_entry} → BUY_DOWN",
                metrics,
            )

        return Decision(
            Action.HOLD,
            f"spread={sp:+.2f} below threshold={self.cfg.spread_entry}",
            metrics,
        )

    def on_end(self, window_id: str, outcome: str) -> None:
        pass  # nothing to update for a stateless threshold strategy


# Explicit export so run_backtest.py can find the class
STRATEGY_CLASS = ThresholdStrategy
