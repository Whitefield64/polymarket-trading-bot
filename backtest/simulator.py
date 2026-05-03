"""
Position simulation for backtesting binary markets.

Models one position per 5-minute window. Settlement math:
  - spread > 0 at time_left=0 → UP wins, DOWN loses
  - spread < 0 at time_left=0 → DOWN wins, UP loses
  - Winner PnL = (size_usdc / entry_price) * 1.0 - size_usdc
    (you bought shares at entry_price; each share resolves to $1.00)
  - Loser PnL  = -size_usdc
    (shares resolve to $0.00, you lose your stake)

Only one position open at a time per window. The engine is responsible
for not calling open() when has_position() is True.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BacktestPosition:
    """Record of a completed position."""
    window_id:   str
    side:        str    # "up" or "down"
    entry_time:  int    # time_left when opened
    entry_price: float
    size_usdc:   float
    exit_time:   Optional[int]   = None
    exit_price:  Optional[float] = None
    exit_reason: str             = ""   # "settled", "close_signal"
    pnl:         Optional[float] = None

    @property
    def shares(self) -> float:
        return self.size_usdc / self.entry_price

    def as_dict(self) -> dict:
        return {
            "window_id":   self.window_id,
            "side":        self.side,
            "entry_time":  self.entry_time,
            "entry_price": self.entry_price,
            "size_usdc":   self.size_usdc,
            "exit_time":   self.exit_time,
            "exit_price":  self.exit_price,
            "exit_reason": self.exit_reason,
            "pnl":         self.pnl,
        }


class BacktestSimulator:
    """
    Manages one position within a single 5-minute window simulation.

    Typical lifecycle:
        sim = BacktestSimulator(window_id, size_usdc=5.0)
        if not sim.has_position():
            sim.open("up", price=0.55, time_left=250)
        ...
        sim.settle(final_spread=-2.5)  # or sim.close("close_signal", price, time)
        closed_pos = sim.position
    """

    def __init__(self, window_id: str, size_usdc: float = 5.0):
        self.window_id  = window_id
        self.size_usdc  = size_usdc
        self._position: Optional[BacktestPosition] = None
        self.closed_positions: list[BacktestPosition] = []

    # ── State ─────────────────────────────────────────────────────────────

    def has_position(self) -> bool:
        return self._position is not None

    @property
    def position(self) -> Optional[BacktestPosition]:
        return self._position

    def current_pnl(self, current_price: float) -> float:
        """Unrealised PnL at the given current price."""
        if not self._position:
            return 0.0
        current_value = self._position.shares * current_price
        return current_value - self._position.size_usdc

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def open(self, side: str, price: float, time_left: int) -> BacktestPosition:
        """Open a new position. Raises if one is already open."""
        if self._position is not None:
            raise RuntimeError("Cannot open a new position while one is already open")
        self._position = BacktestPosition(
            window_id   = self.window_id,
            side        = side,
            entry_time  = time_left,
            entry_price = price,
            size_usdc   = self.size_usdc,
        )
        return self._position

    def close(self, reason: str, exit_price: float, time_left: int) -> BacktestPosition:
        """
        Close the current position early (e.g. strategy signalled CLOSE).
        PnL is computed at exit_price.
        """
        if not self._position:
            raise RuntimeError("No open position to close")
        pos = self._position
        pos.exit_time   = time_left
        pos.exit_price  = exit_price
        pos.exit_reason = reason
        # Realised PnL at exit_price (not settlement price)
        pos.pnl = (pos.shares * exit_price) - pos.size_usdc
        self.closed_positions.append(pos)
        self._position = None
        return pos

    def settle(self, final_spread: float) -> BacktestPosition:
        """
        Settle at end of window using the final spread.

        final_spread > 0 → UP wins (price closed above target)
        final_spread < 0 → DOWN wins (price closed below target)
        final_spread == 0 → treated as UP wins (edge case; extremely rare)
        """
        if not self._position:
            # Nothing to settle — return a dummy record
            return BacktestPosition(
                window_id=self.window_id, side="", entry_time=0,
                entry_price=0, size_usdc=0, exit_time=0,
                exit_price=0, exit_reason="no_trade", pnl=0.0,
            )
        pos = self._position
        pos.exit_time   = 0
        pos.exit_price  = 1.0  # winning token resolves to $1.00
        pos.exit_reason = "settled"

        up_won = final_spread >= 0
        if (pos.side == "up" and up_won) or (pos.side == "down" and not up_won):
            # Winner: each share worth $1.00
            pos.pnl = pos.shares * 1.0 - pos.size_usdc
        else:
            # Loser: shares worth $0.00
            pos.pnl = -pos.size_usdc

        self.closed_positions.append(pos)
        self._position = None
        return pos
