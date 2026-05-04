"""
Live/paper trading engine.

Bridges the BTC 5-min market feed to a strategy and (optionally) real orders.

Paper mode (PAPER_MODE=true, default):
  - Tracks virtual positions via BacktestSimulator
  - No real orders placed
  - Compact terminal output: status line + events only + per-window summary

Live mode (PAPER_MODE=false):
  - Calls bot.place_order() / bot.cancel_order() for real
  - Same position tracking for PnL display
  - Requires credentials in .env

The strategy is completely unaware of paper vs live — it sees identical
MarketState objects and returns Decisions in both modes.

Usage (via run_paper.py — don't call directly):
    engine = TraderEngine(strategy, paper_mode=True)
    asyncio.run(engine.run())
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from backtest.report import BacktestReport
from backtest.simulator import BacktestSimulator
from strategies.base import Action, BaseStrategy, Decision, MarketState
from trader.market_feed import BTC5mMarketFeed

logger = logging.getLogger(__name__)

_STATUS_WIDTH = 110  # pad status line to this width so \r overwrites fully


class TraderEngine:
    """
    Unified paper/live trading engine.

    Args:
        strategy:    Instantiated strategy object.
        paper_mode:  True = track virtual positions only. False = real orders.
        size_usdc:   USDC per trade.
        save_ticks:  If True, save ticks.csv + summary.json to experiments/ on exit.
        continuous:  If True, loop across windows until Ctrl+C (one report for all).
    """

    def __init__(
        self,
        strategy:   BaseStrategy,
        paper_mode: bool  = True,
        size_usdc:  float = 1.0,
        save_ticks: bool  = True,
        continuous: bool  = False,
    ):
        self.strategy    = strategy
        self.paper_mode  = paper_mode
        self.size_usdc   = size_usdc
        self.save_ticks  = save_ticks
        self._continuous = continuous

        self._feed:   BTC5mMarketFeed           = BTC5mMarketFeed()
        self._sim:    Optional[BacktestSimulator] = None
        self._bot     = None  # only created in live mode
        self._report: BacktestReport             = self._new_report()

        # Per-window tick buffer.
        # Flushed to _report.tick_log only on successful window settlement,
        # so a Ctrl+C mid-window drops the partial window cleanly.
        self._window_tick_buffer: list[dict] = []

        # Whether the last printed line was a \r status line
        # (needs a real \n before any permanent output).
        self._on_status_line: bool = False

        self._window_num: int = 0
        self._last_logged_time_left: int = -1  # dedup: skip same-second HOLD ticks

    # ── Helpers ────────────────────────────────────────────────────────────

    def _new_report(self) -> BacktestReport:
        return BacktestReport(
            strategy_name = self.strategy.__class__.__name__,
            mode          = "paper" if self.paper_mode else "live",
            size_usdc     = self.size_usdc,
            params        = self.strategy.get_params(),
        )

    def _newline_if_needed(self) -> None:
        """Move the cursor past the current status line before permanent output."""
        if self._on_status_line:
            print()
            self._on_status_line = False

    def _flush_tick_buffer(self) -> None:
        self._report.tick_log.extend(self._window_tick_buffer)
        self._window_tick_buffer = []

    # ── Entry point ────────────────────────────────────────────────────────

    async def run(self):
        """Connect and run — one window, or continuously until Ctrl+C."""
        mode_label = "PAPER" if self.paper_mode else "LIVE"
        print(f"\n[engine] Starting {mode_label} trading — strategy: {self.strategy.__class__.__name__}")

        if not self.paper_mode:
            await self._init_live_bot()

        if self._continuous:
            await self._run_loop()
        else:
            await self._run_one_window()

    # ── Continuous loop ────────────────────────────────────────────────────

    async def _run_loop(self):
        """
        Run windows back-to-back until Ctrl+C.
        Accumulates all completed windows into a single report saved on exit.
        The partial last window (interrupted mid-run) is silently dropped.
        """
        prev_window_id: Optional[str] = None
        try:
            while True:
                if prev_window_id is not None:
                    self._newline_if_needed()
                    print(f"\n[engine] Waiting for next window...")
                    await self._wait_for_new_window(prev_window_id)

                self._window_num += 1
                self._window_tick_buffer = []
                self._feed = BTC5mMarketFeed()

                self._newline_if_needed()
                print(f"\n[engine] ═══ Window #{self._window_num} ═══")

                ok = await self._feed.connect()
                if not ok:
                    print("[engine] Could not connect. Retrying in 30s...")
                    await asyncio.sleep(30)
                    continue

                try:
                    prev_window_id = await self._run_window_inner()
                except (KeyboardInterrupt, asyncio.CancelledError):
                    # Drop partial window — buffer is not flushed
                    await self._feed.disconnect()
                    raise
                except Exception as exc:
                    logger.error(f"Window error: {exc}")
                    prev_window_id = self._feed.window_id
                    self._window_tick_buffer.clear()
                    await asyncio.sleep(5)

                await self._feed.disconnect()

        except (KeyboardInterrupt, asyncio.CancelledError):
            self._newline_if_needed()
            print("\n[engine] Stopped by user (Ctrl+C).")

        self._save_and_summarise()

    async def _wait_for_new_window(self, old_window_id: str, timeout: int = 360):
        """Poll Gamma API every 5s until a different window slug is live."""
        from src.gamma_client import GammaClient
        gamma = GammaClient()
        loop  = asyncio.get_running_loop()
        elapsed = 0
        while elapsed < timeout:
            await asyncio.sleep(5)
            elapsed += 5
            try:
                market = await loop.run_in_executor(
                    None, gamma.get_current_5m_btc_market
                )
                if market:
                    slug = market.get("slug", "")
                    if slug and slug != old_window_id:
                        print(f"[engine] New window detected: {slug}")
                        return
            except Exception as exc:
                logger.debug(f"Gamma poll error: {exc}")
        raise RuntimeError(f"Timed out waiting for new window after '{old_window_id}'")

    # ── Single-window mode ─────────────────────────────────────────────────

    async def _run_one_window(self):
        """Connect, run one window, disconnect, save."""
        self._window_num = 1
        self._window_tick_buffer = []

        ok = await self._feed.connect()
        if not ok:
            print("[engine] Could not connect to market feed. Exiting.")
            return
        try:
            await self._run_window_inner()
        finally:
            # In single-window mode flush any partial ticks too
            self._flush_tick_buffer()
            await self._feed.disconnect()
            self._save_and_summarise()

    # ── Window inner loop ──────────────────────────────────────────────────

    async def _run_window_inner(self) -> str:
        """
        Run strategy over the active feed window.
        On natural completion (time_left=0): flushes tick buffer and prints summary.
        Returns the window_id.
        """
        window_id = self._feed.window_id
        self._sim = BacktestSimulator(window_id=window_id, size_usdc=self.size_usdc)
        self._last_logged_time_left = -1

        if not self.paper_mode and self._bot:
            await self._bot.cancel_all_orders()

        self.strategy.on_start(window_id)

        mode_label = "PAPER" if self.paper_mode else "LIVE"
        print(f"[engine] Window: {window_id} | mode: {mode_label}")
        print()

        last_state: Optional[MarketState] = None

        async for state in self._feed.stream():
            # Inject position context into state
            if self._sim.has_position():
                side       = self._sim.position.side
                curr_price = state.up_price if side == "up" else state.down_price
                state.has_position   = True
                state.position_side  = side
                state.position_entry = self._sim.position.entry_price
                state.position_pnl   = self._sim.current_pnl(curr_price)
            else:
                state.has_position   = False
                state.position_side  = None
                state.position_entry = 0.0
                state.position_pnl   = 0.0

            decision = self.strategy.tick(state)
            await self._execute(decision, state)
            self._log_tick(state, decision)
            last_state = state

        # Window ended naturally (time_left reached 0)
        if last_state is not None:
            outcome = "up" if last_state.spread >= 0 else "down"
            closed  = self._sim.settle(last_state.spread)
            if closed.exit_reason == "no_trade" and self._sim.closed_positions:
                closed = self._sim.closed_positions[-1]
            trade_dict = closed.as_dict()
            self._report.trade_log.append(trade_dict)
            self.strategy.on_end(window_id, outcome)
            # Commit this window's ticks to the report
            self._flush_tick_buffer()
            self._print_window_summary(outcome, last_state, trade_dict)

        return window_id

    # ── Terminal output ────────────────────────────────────────────────────

    def _log_tick(self, state: MarketState, decision: Decision):
        """
        Per-tick output:
          - Non-HOLD actions → print a permanent event line
          - Always → overwrite the status line in place
          - Always → buffer the tick for ticks.csv
        """
        if decision.action != Action.HOLD:
            self._print_event(state, decision)

        self._print_status(state)

        is_important   = decision.action != Action.HOLD
        new_second     = state.time_left != self._last_logged_time_left
        if is_important or new_second:
            tick = {
                "window_id":      state.window_id,
                "time_left":      state.time_left,
                "target_btc":     state.target_btc,
                "live_btc":       state.live_btc,
                "spread":         state.spread,
                "up_price":       state.up_price,
                "down_price":     state.down_price,
                "has_position":   int(state.has_position),
                "position_side":  state.position_side or "",
                "position_entry": state.position_entry,
                "position_pnl":   state.position_pnl,
                "action":         decision.action.value,
                "reasoning":      decision.reasoning,
            }
            tick.update(decision.metrics)
            self._window_tick_buffer.append(tick)
            if new_second:
                self._last_logged_time_left = state.time_left

    def _print_status(self, state: MarketState) -> None:
        """Overwrite the current line with a compact live market status."""
        if state.has_position:
            pos_str = (
                f"│ {state.position_side.upper()}@{state.position_entry:.3f} "
                f"pnl={state.position_pnl:+.4f}"
            )
        else:
            pos_str = "│ no position"

        mode = "P" if self.paper_mode else "L"
        line = (
            f"  [{mode}] t={state.time_left:3d} "
            f"│ spread={state.spread:+7.2f} "
            f"│ up={state.up_price:.3f} dn={state.down_price:.3f} "
            f"{pos_str}"
        )
        print(f"\r{line:<{_STATUS_WIDTH}}", end="", flush=True)
        self._on_status_line = True

    def _print_event(self, state: MarketState, decision: Decision) -> None:
        """Print a permanent line for a non-HOLD action (BUY / CLOSE)."""
        self._newline_if_needed()

        mode = "P" if self.paper_mode else "L"
        metrics_str = "  ".join(f"{k}={v}" for k, v in decision.metrics.items())
        metric_part = f"  │ {metrics_str}" if metrics_str else ""

        if decision.action == Action.BUY_UP:
            price = decision.price if decision.price is not None else state.up_price
            label = "▶ BUY_UP "
            detail = f"entry={price:.3f}  spread={state.spread:+.2f}"

        elif decision.action == Action.BUY_DOWN:
            price = decision.price if decision.price is not None else state.down_price
            label = "▶ BUY_DN "
            detail = f"entry={price:.3f}  spread={state.spread:+.2f}"

        elif decision.action == Action.CLOSE:
            exit_p = state.up_price if state.position_side == "up" else state.down_price
            label  = "◀ CLOSE  "
            detail = f"exit={exit_p:.3f}  pnl={state.position_pnl:+.4f}"

        else:
            label  = f"  {decision.action.value:<9}"
            detail = ""

        print(
            f"  [{mode}] {label} t={state.time_left:3d}  {detail}"
            f"{metric_part}  —  {decision.reasoning}"
        )

    def _print_window_summary(
        self,
        outcome: str,
        last_state: MarketState,
        trade: dict,
    ) -> None:
        """Print a summary block after a window settles."""
        self._newline_if_needed()

        sep    = "─" * 58
        pnl    = trade.get("pnl")
        reason = trade.get("exit_reason", "n/a")
        side   = trade.get("side", "")
        entry  = trade.get("entry_price")
        exit_p = trade.get("exit_price")

        pnl_str = f"${pnl:+.4f}" if pnl is not None else "n/a"

        if reason == "no_trade":
            trade_line = "no trade"
        else:
            entry_str = f"{entry:.3f}" if entry else "?"
            exit_str  = f"{exit_p:.3f}" if exit_p else "?"
            trade_line = (
                f"{side.upper():<5}  entry={entry_str} → {reason}={exit_str}"
                f"  pnl={pnl_str}"
            )

        print()
        print(f"  {sep}")
        print(f"  Window #{self._window_num}: {last_state.window_id}")
        print(f"  Outcome : {outcome.upper():<5}  spread={last_state.spread:+.2f}")
        print(f"  Trade   : {trade_line}")
        print(f"  {sep}")

    # ── Save / summarise ───────────────────────────────────────────────────

    def _save_and_summarise(self) -> None:
        if self.save_ticks and self._report.tick_log:
            if self.paper_mode:
                path = Path("experiments/paper")
            else:
                path = Path("experiments/live")
            out = self._report.save(path)
            print(f"\n[engine] Experiment saved → {out}")
        self._report.print_summary()

    # ── Execution ──────────────────────────────────────────────────────────

    async def _execute(self, decision: Decision, state: MarketState):
        if self.paper_mode:
            await self._execute_paper(decision, state)
        else:
            await self._execute_live(decision, state)

    async def _execute_paper(self, decision: Decision, state: MarketState):
        if decision.action == Action.BUY_UP and not self._sim.has_position():
            price = decision.price if decision.price is not None else state.up_price
            self._sim.open("up", price, state.time_left)

        elif decision.action == Action.BUY_DOWN and not self._sim.has_position():
            price = decision.price if decision.price is not None else state.down_price
            self._sim.open("down", price, state.time_left)

        elif decision.action == Action.CLOSE and self._sim.has_position():
            exit_price = (
                state.up_price if self._sim.position.side == "up"
                else state.down_price
            )
            self._sim.close("close_signal", exit_price, state.time_left)

    async def _execute_live(self, decision: Decision, state: MarketState):
        if not self._bot:
            return

        try:
            if decision.action == Action.BUY_UP and not self._sim.has_position():
                price  = decision.price if decision.price is not None else state.up_price
                result = await self._bot.place_order(
                    token_id=self._feed._up_token,
                    price=round(price + 0.01, 2),
                    size=round(self.size_usdc / price, 4),
                    side="BUY",
                    fee_rate_bps=1000,
                )
                if result.success:
                    self._sim.open("up", price, state.time_left)
                    self._newline_if_needed()
                    print(f"[engine] ORDER PLACED: BUY_UP @ {price:.3f} | {result}")
                else:
                    logger.warning(f"BUY_UP order failed: {result}")

            elif decision.action == Action.BUY_DOWN and not self._sim.has_position():
                price  = decision.price if decision.price is not None else state.down_price
                result = await self._bot.place_order(
                    token_id=self._feed._down_token,
                    price=round(price + 0.01, 2),
                    size=round(self.size_usdc / price, 4),
                    side="BUY",
                    fee_rate_bps=1000,
                )
                if result.success:
                    self._sim.open("down", price, state.time_left)
                    self._newline_if_needed()
                    print(f"[engine] ORDER PLACED: BUY_DOWN @ {price:.3f} | {result}")
                else:
                    logger.warning(f"BUY_DOWN order failed: {result}")

            elif decision.action == Action.CLOSE and self._sim.has_position():
                pos      = self._sim.position
                token_id = (
                    self._feed._up_token if pos.side == "up"
                    else self._feed._down_token
                )
                exit_price = state.up_price if pos.side == "up" else state.down_price
                result = await self._bot.place_order(
                    token_id=token_id,
                    price=round(exit_price - 0.01, 2),
                    size=round(pos.shares, 4),
                    side="SELL",
                    fee_rate_bps=1000,
                )
                if result.success:
                    self._sim.close("close_signal", exit_price, state.time_left)
                    self._newline_if_needed()
                    print(f"[engine] ORDER PLACED: SELL {pos.side} @ {exit_price:.3f} | {result}")
                else:
                    logger.warning(f"CLOSE order failed: {result}")

        except Exception as exc:
            logger.error(f"Order execution error: {exc}")

    # ── Live bot setup ─────────────────────────────────────────────────────

    async def _init_live_bot(self):
        from src.utils import create_bot_from_env
        try:
            self._bot = create_bot_from_env()
            print("[engine] Live bot initialised from .env credentials")
        except Exception as exc:
            print(f"[engine] ERROR: could not create live bot: {exc}")
            raise
