"""
Backtest result reporting.

BacktestReport holds all per-tick data and per-trade results,
computes aggregate statistics, and writes output to experiments/.

Output layout:
    experiments/{strategy}_{timestamp}/
        ticks.csv    — one row per second, all market data + metrics + decision
        summary.json — aggregate stats (PnL, win rate, drawdown, etc.)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass
class BacktestReport:
    strategy_name: str
    mode:          str                  = "backtest"   # "backtest", "paper", or "live"
    size_usdc:     float                = 1.0
    params:        dict[str, Any]       = field(default_factory=dict)
    tick_log:      list[dict[str, Any]] = field(default_factory=list)
    trade_log:     list[dict[str, Any]] = field(default_factory=list)

    # ── Summary stats ──────────────────────────────────────────────────────

    def summary(self) -> dict[str, Any]:
        """Compute aggregate statistics from closed trades."""
        if not self.trade_log:
            return {
                "strategy":    self.strategy_name,
                "mode":        self.mode,
                "size_usdc":   self.size_usdc,
                "params":      self.params,
                "windows":     0,
                "trades":      0,
                "win_rate":    0.0,
                "total_pnl":   0.0,
                "avg_pnl":     0.0,
                "max_drawdown": 0.0,
            }

        trades = [t for t in self.trade_log if t["exit_reason"] != "no_trade"]
        pnls   = [t["pnl"] for t in trades if t["pnl"] is not None]

        wins        = sum(1 for p in pnls if p > 0)
        total_pnl   = sum(pnls)
        avg_pnl     = total_pnl / len(pnls) if pnls else 0.0

        # Running drawdown
        max_drawdown = 0.0
        peak         = 0.0
        running      = 0.0
        for p in pnls:
            running += p
            if running > peak:
                peak = running
            dd = peak - running
            if dd > max_drawdown:
                max_drawdown = dd

        windows = len({t["window_id"] for t in self.trade_log})

        return {
            "strategy":     self.strategy_name,
            "mode":         self.mode,
            "size_usdc":    self.size_usdc,
            "params":       self.params,
            "windows":      windows,
            "trades":       len(trades),
            "win_rate":     round(wins / len(trades), 4) if trades else 0.0,
            "total_pnl":    round(total_pnl, 4),
            "avg_pnl":      round(avg_pnl, 4),
            "max_drawdown": round(max_drawdown, 4),
        }

    # ── Output ─────────────────────────────────────────────────────────────

    def save(self, experiments_dir: Path) -> Path:
        """
        Write ticks.csv and summary.json to a timestamped subdirectory.
        Returns the path to that subdirectory.
        """
        ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = experiments_dir / f"{self.strategy_name}_{ts}"
        out_dir.mkdir(parents=True, exist_ok=True)

        # ticks.csv
        if self.tick_log:
            ticks_df = pd.DataFrame(self.tick_log)
            ticks_df.to_csv(out_dir / "ticks.csv", index=False)

        # trades.csv — one row per closed position (settled PnL, not mark-to-market)
        if self.trade_log:
            trades_df = pd.DataFrame(self.trade_log)
            trades_df.to_csv(out_dir / "trades.csv", index=False)

        # summary.json
        with open(out_dir / "summary.json", "w") as f:
            json.dump(self.summary(), f, indent=2)

        return out_dir

    def print_summary(self) -> None:
        """Print a compact summary table to stdout."""
        s = self.summary()
        print()
        print("=" * 50)
        print(f"  Strategy   : {s['strategy']}")
        print(f"  Mode       : {s['mode']}")
        print(f"  Trade size : ${s['size_usdc']:.2f}")
        if s["params"]:
            for k, v in s["params"].items():
                print(f"  {k:<12} : {v}")
        print(f"  Windows    : {s['windows']}")
        print(f"  Trades     : {s['trades']}")
        print(f"  Win rate   : {s['win_rate']:.1%}")
        print(f"  Total PnL  : ${s['total_pnl']:+.2f}")
        print(f"  Avg PnL    : ${s['avg_pnl']:+.4f}")
        print(f"  Max DD     : ${s['max_drawdown']:.2f}")
        print("=" * 50)
