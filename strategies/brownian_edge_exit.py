"""
Brownian-motion strategy with edge-gone exit.

Entry: Enter when model edge > min_edge.
Exit:  Close immediately when the edge on the open side turns negative
       (the market has re-priced to match or beat our model).

This is the edge-gone exit variant. See brownian_motion.py for the
profit-take variant, which holds until a fixed PnL target is reached.

The retry mechanism guards against live-trading execution delays:
if a CLOSE signal is sent but the position is still open after
retry_timeout seconds, the edge is re-evaluated and the signal is
re-issued.

Usage:
    uv run python run_backtest.py --strategy brownian_edge_exit
    uv run python run_paper.py   --strategy brownian_edge_exit
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from strategies.base import Action, BaseStrategy, Decision, MarketState


DEFAULT_SIGMA: float = 3.9856


def _norm_cdf(z: float) -> float:
    """Standard normal CDF via math.erfc."""
    return 0.5 * math.erfc(-z / math.sqrt(2))


def predict_single(time_left: float, spread: float, sigma: float = DEFAULT_SIGMA) -> dict:
    if time_left <= 0:
        p_up = 1.0 if spread > 0 else (0.5 if spread == 0 else 0.0)
    else:
        z    = spread / (sigma * math.sqrt(time_left) + 1e-10)
        p_up = _norm_cdf(z)

    return {
        "P_UP":      round(p_up, 4),
        "P_DOWN":    round(1 - p_up, 4),
        "sigma_tau": round(sigma * math.sqrt(max(time_left, 0)), 2),
    }


@dataclass
class EdgeExitConfig:
    sigma: float = DEFAULT_SIGMA
    min_edge: float = 0.5
    min_time_left: int = 10
    max_time_left: int = 290


class EdgeExitStrategy(BaseStrategy):
    """
    Brownian-motion strategy with edge-gone exit.

    Entry
    -----
    Enter the side whose model edge exceeds min_edge.

    Exit
    ----
    Close as soon as the edge on the open side turns negative.
    The retry_timeout guards against dropped close orders in live trading:
    if the position is still open after that many seconds, re-issue CLOSE.

    Metrics logged to ticks.csv
    ---------------------------
    p_up, p_down, edge_up, edge_down, sigma_tau
    """

    def __init__(self, config: EdgeExitConfig = EdgeExitConfig()):
        self.cfg = config
        self._close_request_time: float | None = None
        self.retry_timeout: float = 1.0

    def on_tick(self, state: MarketState) -> Decision:
        sp = state.spread
        t  = state.time_left

        pred   = predict_single(t, sp, sigma=self.cfg.sigma)
        p_up   = pred["P_UP"]
        p_down = pred["P_DOWN"]

        edge_up   = round(p_up   - state.up_price,   4)
        edge_down = round(p_down - state.down_price, 4)

        metrics = {
            "p_up":      p_up,
            "p_down":    p_down,
            "edge_up":   edge_up,
            "edge_down": edge_down,
            "sigma_tau": pred["sigma_tau"],
        }

        # ── Exit: edge turned negative ────────────────────────────────────
        if state.has_position:

            # If a close was already requested, wait for it (with retry on timeout)
            if self._close_request_time is not None:
                time_waiting = time.time() - self._close_request_time
                if time_waiting < self.retry_timeout:
                    return Decision(Action.HOLD, f"waiting for close execution ({time_waiting:.1f}s)", metrics)
                # Timeout exceeded — fall through and re-evaluate

            side      = str(state.position_side).lower()
            side_edge = edge_up if side == "up" else edge_down

            if side_edge < 0:
                self._close_request_time = time.time()
                return Decision(Action.CLOSE, f"edge gone: {side_edge:+.4f}", metrics)

            return Decision(Action.HOLD, f"holding {side} | edge={side_edge:+.4f}", metrics)

        # Position closed — reset retry state
        self._close_request_time = None

        # ── Time-window guard ─────────────────────────────────────────────
        if not (self.cfg.min_time_left <= t <= self.cfg.max_time_left):
            return Decision(Action.HOLD, f"t={t} outside window", metrics)

        # ── Entry: take the side with the largest edge > min_edge ─────────
        if edge_up > self.cfg.min_edge and edge_up >= edge_down:
            return Decision(
                Action.BUY_UP,
                f"edge(UP)={edge_up:+.4f} > min_edge={self.cfg.min_edge} → BUY_UP",
                metrics,
            )

        if edge_down > self.cfg.min_edge and edge_down > edge_up:
            return Decision(
                Action.BUY_DOWN,
                f"edge(DOWN)={edge_down:+.4f} > min_edge={self.cfg.min_edge} → BUY_DOWN",
                metrics,
            )

        return Decision(Action.HOLD, "no actionable edge", metrics)

    def on_end(self, window_id: str, outcome: str) -> None:
        pass


STRATEGY_CLASS = EdgeExitStrategy
