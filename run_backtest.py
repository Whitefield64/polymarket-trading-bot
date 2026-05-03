#!/usr/bin/env python3
"""
Run a strategy backtest over the historical CSV dataset.

Usage:
    uv run python run_backtest.py --strategy example_threshold
    uv run python run_backtest.py --strategy example_threshold --size 10
    uv run python run_backtest.py --strategy example_threshold --start 1774979400 --end 1775016900
    uv run python run_backtest.py --strategy example_threshold --quiet

Output:
    Console: per-tick data + metrics + decisions (suppress with --quiet)
    experiments/{StrategyName}_{timestamp}/
        ticks.csv    — every second logged
        summary.json — aggregate stats

The strategy name maps to strategies/{name}.py.
The class inside must be named in CamelCase of the module name,
or you can add a STRATEGY_CLASS attribute to the module:
    STRATEGY_CLASS = MyStrategy
"""

import argparse
import importlib
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DATASETS_DIR   = Path("datasets")
EXPERIMENTS_DIR = Path("experiments/backtest")


def load_strategy(name: str):
    """
    Dynamically import strategies/{name}.py and return an instance.

    Looks for STRATEGY_CLASS attribute first, then tries CamelCase of module name.
    """
    module_path = f"strategies.{name}"
    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError:
        print(f"Error: could not import '{module_path}'. "
              f"Make sure strategies/{name}.py exists.")
        sys.exit(1)

    # Prefer explicit export
    if hasattr(module, "STRATEGY_CLASS"):
        cls = module.STRATEGY_CLASS
    else:
        # Guess CamelCase from module name: example_threshold → ExampleThreshold
        class_name = "".join(part.capitalize() for part in name.split("_"))
        cls = getattr(module, class_name, None)
        if cls is None:
            print(
                f"Error: could not find class '{class_name}' in {module_path}. "
                f"Add STRATEGY_CLASS = YourClass to the module to make it explicit."
            )
            sys.exit(1)

    return cls()


def main():
    parser = argparse.ArgumentParser(
        description="Backtest a strategy over historical BTC 5-min market data."
    )
    parser.add_argument(
        "--strategy", required=True,
        help="Module name in strategies/ (e.g. 'example_threshold')"
    )
    parser.add_argument(
        "--size", type=float, default=3.0,
        help="USDC amount per trade (default: 1.0)"
    )
    parser.add_argument(
        "--start", default=None,
        help="Start window ID or timestamp (inclusive)"
    )
    parser.add_argument(
        "--end", default=None,
        help="End window ID or timestamp (inclusive)"
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress per-tick output (only show summary)"
    )
    args = parser.parse_args()

    if not DATASETS_DIR.exists():
        print(f"Error: datasets directory not found at {DATASETS_DIR}")
        sys.exit(1)

    from backtest.engine import run_backtest

    strategy = load_strategy(args.strategy)
    report   = run_backtest(
        strategy     = strategy,
        datasets_dir = DATASETS_DIR,
        size_usdc    = args.size,
        start        = args.start,
        end          = args.end,
        verbose      = not args.quiet,
    )

    report.print_summary()
    out_dir = report.save(EXPERIMENTS_DIR)
    print(f"\nResults saved to: {out_dir}")


if __name__ == "__main__":
    main()
