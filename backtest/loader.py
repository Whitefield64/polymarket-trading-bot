"""
Dataset loader for BTC 5-minute market CSV files.

Each file in datasets/ represents one 5-minute market window (~300 rows, 1 per second).
Columns: Time Left, Target BTC, Live BTC, Spread, UP Price, DOWN Price

Usage:
    from backtest.loader import iter_windows, load_window, list_windows

    for window_id, df in iter_windows(Path("datasets")):
        print(window_id, len(df))
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator, Optional

import pandas as pd

# Expected column names after normalisation (lowercase, underscores)
COLUMNS = ["time_left", "target_btc", "live_btc", "spread", "up_price", "down_price"]


def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename CSV header to snake_case regardless of original capitalisation."""
    df.columns = (
        df.columns
        .str.strip()
        .str.lower()
        .str.replace(" ", "_")
    )
    return df


def _window_ts(window_id: str) -> int:
    """Extract the Unix timestamp from a window ID like 'btc-updown-5m-1774979400'."""
    match = re.search(r"(\d+)$", window_id)
    if not match:
        raise ValueError(f"Cannot parse timestamp from window_id: {window_id!r}")
    return int(match.group(1))


def list_windows(datasets_dir: Path) -> list[str]:
    """
    Return all window IDs in datasets_dir, sorted chronologically.

    A window ID is the filename without the .csv extension,
    e.g. 'btc-updown-5m-1774979400'.
    """
    files = sorted(
        datasets_dir.glob("btc-updown-5m-*.csv"),
        key=lambda p: _window_ts(p.stem),
    )
    return [f.stem for f in files]


def load_window(datasets_dir: Path, window_id: str) -> pd.DataFrame:
    """
    Load a single CSV window into a DataFrame.

    - Renames columns to snake_case
    - Drops rows where up_price == 0 (market not yet open / no orderbook)
    - Sorts descending by time_left so row 0 = start of window
    - Resets index

    Returns DataFrame with columns: time_left, target_btc, live_btc,
    spread, up_price, down_price
    """
    path = datasets_dir / f"{window_id}.csv"
    df = pd.read_csv(path)
    df = _normalise_columns(df)

    # Validate columns
    missing = set(COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"CSV {path} is missing columns: {missing}")

    # Drop rows where orderbook hadn't stabilised yet
    df = df[df["up_price"] > 0].copy()

    # Sort by time_left descending (start → end)
    df = df.sort_values("time_left", ascending=False).reset_index(drop=True)

    return df[COLUMNS]


def iter_windows(
    datasets_dir: Path,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> Iterator[tuple[str, pd.DataFrame]]:
    """
    Yield (window_id, DataFrame) pairs in chronological order.

    start / end: optional window_id bounds (inclusive).
    If provided, only windows with timestamps in [start_ts, end_ts] are yielded.
    """
    windows = list_windows(datasets_dir)

    if not windows:
        return

    start_ts = _window_ts(start) if start else None
    end_ts   = _window_ts(end)   if end   else None

    for window_id in windows:
        ts = _window_ts(window_id)
        if start_ts is not None and ts < start_ts:
            continue
        if end_ts is not None and ts > end_ts:
            continue
        yield window_id, load_window(datasets_dir, window_id)
