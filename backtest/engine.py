"""
Backtest engine.

Iterates over historical CSV windows, feeds each second to the strategy,
simulates positions, and collects full per-tick logs.

Usage (via run_backtest.py — don't call directly):
    from backtest.engine import run_backtest
    report = run_backtest(strategy, datasets_dir, size_usdc=5.0, verbose=True)
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from backtest.loader import iter_windows
from backtest.report import BacktestReport
from backtest.simulator import BacktestSimulator
from strategies.base import Action, BaseStrategy, MarketState


def run_backtest(
    strategy:      BaseStrategy,
    datasets_dir:  Path,
    size_usdc:     float = 1.0,
    start:         Optional[str] = None,
    end:           Optional[str] = None,
    verbose:       bool = True,
) -> BacktestReport:
    """
    Run strategy over all windows in datasets_dir.

    Args:
        strategy:     Instantiated strategy object.
        datasets_dir: Path to the datasets/ directory.
        size_usdc:    USDC amount per trade.
        start:        Optional window_id to start from (inclusive).
        end:          Optional window_id to stop at (inclusive).
        verbose:      If True, print every tick to stdout.

    Returns:
        BacktestReport with full tick_log and trade_log.
    """
    strategy_name = strategy.__class__.__name__
    report = BacktestReport(
        strategy_name = strategy_name,
        mode          = "backtest",
        size_usdc     = size_usdc,
        params        = strategy.get_params(),
    )

    windows = list(iter_windows(datasets_dir, start=start, end=end))
    total   = len(windows)

    if total == 0:
        print("No windows found. Check datasets_dir and --start/--end filters.")
        return report

    print(f"Backtesting {strategy_name} over {total} windows...")
    print()

    for i, (window_id, df) in enumerate(windows, 1):
        _run_window(
            strategy   = strategy,
            window_id  = window_id,
            df         = df,
            size_usdc  = size_usdc,
            report     = report,
            verbose    = verbose,
            window_num = i,
            total      = total,
        )

    return report


def _run_window(
    strategy:   BaseStrategy,
    window_id:  str,
    df,
    size_usdc:  float,
    report:     BacktestReport,
    verbose:    bool,
    window_num: int,
    total:      int,
) -> None:
    """Run one 5-minute window through the strategy."""

    if verbose:
        print(f"── Window {window_num}/{total}: {window_id} ({len(df)} ticks) ──")

    strategy.on_start(window_id)
    sim = BacktestSimulator(window_id=window_id, size_usdc=size_usdc)

    for row in df.itertuples(index=False):
        # Determine current side price for unrealised PnL
        if sim.has_position():
            side        = sim.position.side
            curr_price  = row.up_price if side == "up" else row.down_price
            unreal_pnl  = sim.current_pnl(curr_price)
        else:
            side       = None
            unreal_pnl = 0.0

        state = MarketState(
            time_left      = int(row.time_left),
            target_btc     = float(row.target_btc),
            live_btc       = float(row.live_btc),
            spread         = float(row.spread),
            up_price       = float(row.up_price),
            down_price     = float(row.down_price),
            has_position   = sim.has_position(),
            position_side  = side,
            position_entry = sim.position.entry_price if sim.has_position() else 0.0,
            position_pnl   = unreal_pnl,
            window_id      = window_id,
        )

        decision = strategy.tick(state)

        # ── Execute decision ───────────────────────────────────────────────
        if decision.action == Action.BUY_UP and not sim.has_position():
            price = decision.price if decision.price is not None else row.up_price
            sim.open("up", price, int(row.time_left))

        elif decision.action == Action.BUY_DOWN and not sim.has_position():
            price = decision.price if decision.price is not None else row.down_price
            sim.open("down", price, int(row.time_left))

        elif decision.action == Action.CLOSE and sim.has_position():
            exit_price = (
                row.up_price if sim.position.side == "up" else row.down_price
            )
            sim.close("close_signal", exit_price, int(row.time_left))

        # ── Console output (every tick) ────────────────────────────────────
        if verbose:
            metrics_str = "  ".join(f"{k}={v}" for k, v in decision.metrics.items())
            pos_str = ""
            if sim.has_position():
                pos_str = f" | pos={sim.position.side}@{sim.position.entry_price:.3f} pnl={unreal_pnl:+.4f}"
            elif decision.action in (Action.BUY_UP, Action.BUY_DOWN):
                # Just opened
                pos_str = f" | OPENED {decision.action.value}"
            print(
                f"  t={state.time_left:3d} | "
                f"spread={state.spread:+7.2f} | "
                f"up={state.up_price:.3f} dn={state.down_price:.3f}"
                + (f" | {metrics_str}" if metrics_str else "")
                + f"{pos_str}"
                + f" → {decision.action.value} ({decision.reasoning})"
            )

        # ── Log tick ───────────────────────────────────────────────────────
        tick = {
            "window_id":   window_id,
            "time_left":   state.time_left,
            "target_btc":  state.target_btc,
            "live_btc":    state.live_btc,
            "spread":      state.spread,
            "up_price":    state.up_price,
            "down_price":  state.down_price,
            "has_position": int(state.has_position),
            "position_side": state.position_side or "",
            "position_entry": state.position_entry,
            "position_pnl": state.position_pnl,
            "action":      decision.action.value,
            "reasoning":   decision.reasoning,
        }
        tick.update(decision.metrics)
        report.tick_log.append(tick)

    # ── End of window: settle any open position ────────────────────────────
    final = df.iloc[-1]
    final_spread = float(final.spread)
    outcome = "up" if final_spread >= 0 else "down"

    dummy = sim.settle(final_spread)
    # sim.closed_positions holds all exits: early closes + settlement.
    # If empty, no trade was ever made — record the no_trade dummy.
    if sim.closed_positions:
        for pos in sim.closed_positions:
            report.trade_log.append(pos.as_dict())
        closed_pos = sim.closed_positions[-1]
    else:
        report.trade_log.append(dummy.as_dict())
        closed_pos = dummy

    strategy.on_end(window_id, outcome)

    if verbose:
        pnl_str = f"${closed_pos.pnl:+.4f}" if closed_pos.pnl is not None else "n/a"
        print(
            f"  → Window end: outcome={outcome} | "
            f"exit_reason={closed_pos.exit_reason} | pnl={pnl_str}"
        )
        print()
