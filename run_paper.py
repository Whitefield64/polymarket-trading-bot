#!/usr/bin/env python3
"""
Run paper or live trading against the real Polymarket market.

Paper mode (default, safe):
  - Tracks virtual positions and PnL — no real money moves
  - Per-tick output identical to backtest verbose mode
  - Ticks auto-saved to experiments/paper_{Strategy}_{timestamp}/

Live mode (real money):
  - Set PAPER_MODE=false in .env
  - Requires POLY_PRIVATE_KEY and POLY_SAFE_ADDRESS in .env
  - Confirmation prompt required before starting

Usage:
    # Paper trading (default):
    uv run python run_paper.py --strategy example_threshold

    # Live trading (set PAPER_MODE=false in .env first):
    uv run python run_paper.py --strategy example_threshold

    # Override size:
    uv run python run_paper.py --strategy example_threshold --size 10

The strategy name maps to strategies/{name}.py — same convention as run_backtest.py.
"""

import argparse
import asyncio
import importlib
import logging
import os
import sys

from dotenv import load_dotenv

logging.getLogger("src.websocket_client").setLevel(logging.WARNING)

load_dotenv()

# Paper mode: true unless explicitly disabled in .env
PAPER_MODE = os.environ.get("PAPER_MODE", "true").lower() != "false"


def load_strategy(name: str):
    """Dynamically import strategies/{name}.py and return an instance."""
    module_path = f"strategies.{name}"
    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError:
        print(f"Error: could not import '{module_path}'. "
              f"Make sure strategies/{name}.py exists.")
        sys.exit(1)

    if hasattr(module, "STRATEGY_CLASS"):
        cls = module.STRATEGY_CLASS
    else:
        class_name = "".join(part.capitalize() for part in name.split("_"))
        cls = getattr(module, class_name, None)
        if cls is None:
            print(
                f"Error: could not find class '{class_name}' in {module_path}. "
                f"Add STRATEGY_CLASS = YourClass to the module."
            )
            sys.exit(1)

    return cls()


def confirm_live():
    """Require explicit typed confirmation before placing real orders."""
    print()
    print("=" * 60)
    print("  ⚠  LIVE TRADING MODE — REAL MONEY WILL BE SPENT  ⚠")
    print("=" * 60)
    print(f"  Strategy : {STRATEGY_NAME}")
    print(f"  Size     : ${TRADE_SIZE:.2f} USDC per trade")
    print()
    answer = input("  Type YES to confirm, anything else to abort: ").strip()
    if answer != "YES":
        print("Aborted.")
        sys.exit(0)
    print()


# Module-level vars set in main() for use in confirm_live()
STRATEGY_NAME = ""
TRADE_SIZE    = 3.0


def main():
    global STRATEGY_NAME, TRADE_SIZE

    parser = argparse.ArgumentParser(
        description="Run paper or live trading with a strategy."
    )
    parser.add_argument(
        "--strategy", required=True,
        help="Module name in strategies/ (e.g. 'example_threshold')"
    )
    parser.add_argument(
        "--size", type=float, default=3.0,
        help="USDC amount per trade (default: 3.0)"
    )
    parser.add_argument(
        "--no-save", action="store_true",
        help="Don't save tick log to experiments/"
    )
    parser.add_argument(
        "--continuous", action="store_true",
        help="Keep running across windows until Ctrl+C (saves one experiment dir per window)"
    )
    args = parser.parse_args()

    STRATEGY_NAME = args.strategy
    TRADE_SIZE    = args.size

    strategy = load_strategy(args.strategy)

    mode_label = "PAPER" if PAPER_MODE else "LIVE"
    print(f"Mode: {mode_label} | Strategy: {strategy.__class__.__name__} | Size: ${args.size:.2f}")

    if not PAPER_MODE:
        confirm_live()

    from trader.engine import TraderEngine
    engine = TraderEngine(
        strategy   = strategy,
        paper_mode = PAPER_MODE,
        size_usdc  = args.size,
        save_ticks = not args.no_save,
        continuous = args.continuous,
    )

    asyncio.run(engine.run())


if __name__ == "__main__":
    main()
