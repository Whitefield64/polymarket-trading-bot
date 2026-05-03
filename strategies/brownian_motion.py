"""
Brownian-motion strategy with market-implied edge.

Entry: enter only when our model disagrees with the market price by at least
       `min_edge` (i.e. we think the outcome is more likely than the market does).

Exit (two triggers, whichever fires first):
  1. Profit-take — close as soon as unrealised PnL > profit_take threshold.
     Accepts a smaller-but-certain gain rather than riding to settlement.
  2. Edge gone  — close when our model edge on the open side turns negative.
     The market has caught up; no reason to keep the risk.

Edge definition
---------------
  edge_up   = P_UP   (our model) - up_price   (market consensus)
  edge_down = P_DOWN (our model) - down_price  (market consensus)

A positive edge means we think the outcome is *more likely* than the market
is pricing.  We enter on positive edge, and exit the moment it turns negative.

Maths
-----
  z     = spread / (σ · √τ + ε)
  P_UP  = Φ(z)       (standard normal CDF)
  P_DOWN= 1 − P_UP

Usage
-----
    uv run python run_backtest.py --strategy brownian_motion
    uv run python run_paper.py   --strategy brownian_motion
"""

from __future__ import annotations

import math
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
class BrownianConfig:
    sigma: float = DEFAULT_SIGMA

    # Minimum edge required to open a position.
    # Edge = our P(side) - market price for that side.
    # 0.05 means we need to think the outcome is at least 5 pp more likely
    # than the market does before entering.
    min_edge: float = 0.5

    # Early profit-taking: close as soon as unrealised PnL exceeds this value.
    # Set to None to disable (hold until edge disappears or window ends).
    # Units are whatever position_pnl is denominated in (typically USDC).
    profit_take: float | None = None   # e.g. close at +0.3 % PnL

    # Time-based guards
    min_time_left: int = 10    # don't enter in the last N seconds
    max_time_left: int = 290   # don't enter in the first few seconds


class BrownianStrategy(BaseStrategy):
    """
    Brownian-motion strategy using market-implied edge for entry and exit.

    Entry
    -----
    Compute edge_up  = P(UP)   − up_price
            edge_down = P(DOWN) − down_price

    Enter the side whose edge exceeds min_edge.  If both do (unusual), take
    the larger edge.

    Exit (two triggers, checked in order each tick)
    ----
    1. Profit-take: if position_pnl >= profit_take, close immediately.
       Takes a guaranteed smaller gain rather than riding all the way to
       settlement. Reduces variance at the cost of some upside.

    2. Edge gone: if the model edge on our side turns negative, close.
       The market has re-priced to match or beat our model — the original
       reason to hold the position no longer exists.

    Metrics logged to ticks.csv
    ---------------------------
    p_up, p_down, edge_up, edge_down, sigma_tau
    """

    def __init__(self, config: BrownianConfig = BrownianConfig()):
        self.cfg = config

    def on_tick(self, state: MarketState) -> Decision:
        sp = state.spread
        t  = state.time_left

        pred   = predict_single(t, sp, sigma=self.cfg.sigma)
        p_up   = pred["P_UP"]
        p_down = pred["P_DOWN"]

        edge_up   = round(p_up   - state.up_price,   4)
        edge_down = round(p_down - state.down_price,  4)

        metrics = {
            "p_up":      p_up,
            "p_down":    p_down,
            "edge_up":   edge_up,
            "edge_down": edge_down,
            "sigma_tau": pred["sigma_tau"],
        }

        # ── Exit: profit-take first, then edge-gone ───────────────────────
        if state.has_position:
            side      = state.position_side          # "up" or "down"
            side_edge = edge_up if side == "up" else edge_down

            # 1. Profit-take — lock in gains early
            if (
                self.cfg.profit_take is not None
                and state.position_pnl >= self.cfg.profit_take
            ):
                return Decision(
                    Action.CLOSE,
                    (
                        f"profit-take: pnl={state.position_pnl:+.4f} >= {self.cfg.profit_take} → CLOSE "
                        f"| edge({side})={side_edge:+.4f} | spread={sp:+.2f}"
                    ),
                    metrics,
                )

            # 2. Edge gone — market has caught up, no reason to hold
            # if side_edge < 0:
            #     return Decision(
            #         Action.CLOSE,
            #         (
            #             f"edge({side})={side_edge:+.4f} turned negative → market has caught up, CLOSE "
            #             f"| spread={sp:+.2f} | pnl={state.position_pnl:+.4f}"
            #         ),
            #         metrics,
            #     )

            return Decision(
                Action.HOLD,
                (
                    f"holding {side} | edge={side_edge:+.4f} still positive "
                    f"| spread={sp:+.2f} | pnl={state.position_pnl:+.4f}"
                ),
                metrics,
            )

        # ── Time-window guard ─────────────────────────────────────────────
        in_window = self.cfg.min_time_left <= t <= self.cfg.max_time_left
        if not in_window:
            return Decision(
                Action.HOLD,
                f"t={t} outside entry window [{self.cfg.min_time_left}, {self.cfg.max_time_left}]",
                metrics,
            )

        # ── Entry: take the side with the largest positive edge ───────────
        best_side = None
        best_edge = self.cfg.min_edge  # must beat this to enter

        if edge_up > best_edge:
            best_side = "up"
            best_edge = edge_up
        if edge_down > best_edge:
            best_side = "down"
            best_edge = edge_down

        if best_side == "up":
            return Decision(
                Action.BUY_UP,
                (
                    f"edge(UP)={edge_up:+.4f} > min_edge={self.cfg.min_edge} "
                    f"| P(UP)={p_up:.4f} vs market={state.up_price:.4f} → BUY_UP"
                ),
                metrics,
            )

        if best_side == "down":
            return Decision(
                Action.BUY_DOWN,
                (
                    f"edge(DOWN)={edge_down:+.4f} > min_edge={self.cfg.min_edge} "
                    f"| P(DOWN)={p_down:.4f} vs market={state.down_price:.4f} → BUY_DOWN"
                ),
                metrics,
            )

        return Decision(
            Action.HOLD,
            (
                f"no edge | edge_up={edge_up:+.4f} edge_down={edge_down:+.4f} "
                f"< min_edge={self.cfg.min_edge} | spread={sp:+.2f}"
            ),
            metrics,
        )

    def on_end(self, window_id: str, outcome: str) -> None:
        pass


STRATEGY_CLASS = BrownianStrategy