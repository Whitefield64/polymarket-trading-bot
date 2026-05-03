"""
BTC 5-minute market feed for live/paper trading.

Discovers the current BTC 5-min market, fetches the official target price
from the Vatic API, subscribes to the CLOB WebSocket for UP/DOWN orderbook
and the live-data WebSocket for the real BTC price, then streams MarketState
objects (same shape as backtest CSV rows) to the caller.

Usage (internal — called by trader/engine.py):
    feed = BTC5mMarketFeed()
    await feed.connect()
    async for state in feed.stream():
        decision = strategy.on_tick(state)
    await feed.disconnect()
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

import requests

from src.gamma_client import GammaClient
from src.websocket_client import MarketWebSocket, OrderbookSnapshot
from strategies.base import MarketState

logger = logging.getLogger(__name__)

VATIC_URL = (
    "https://api.vatic.trading/api/v1/targets/timestamp"
    "?asset=btc&type=5min&timestamp={ts}"
)
LIVE_BTC_WSS = "wss://ws-live-data.polymarket.com"


class BTC5mMarketFeed:
    """
    Thin wrapper around GammaClient + MarketWebSocket that produces MarketState ticks.

    State emitted per WebSocket update:
      - time_left:  seconds remaining (derived from market endDate)
      - target_btc: locked at window start via Vatic API
      - live_btc:   current BTC price from crypto_prices_chainlink feed
      - spread:     live_btc - target_btc
      - up_price:   mid-price of UP token orderbook
      - down_price: mid-price of DOWN token orderbook
      - window_id:  market slug

    One MarketState is emitted each time either orderbook updates.
    You'll typically receive several per second.
    """

    def __init__(self):
        self._gamma      = GammaClient()
        self._ws         = MarketWebSocket()
        self._market:    Optional[dict]  = None
        self._target_btc: float          = 0.0
        self._live_btc:   float          = 0.0
        self._up_price:   float          = 0.5
        self._down_price: float          = 0.5
        self._up_token:   str            = ""
        self._down_token: str            = ""
        self._window_id:  str            = ""
        self._end_ts:     int            = 0
        self._queue:      asyncio.Queue  = asyncio.Queue()
        self._live_btc_task: Optional[asyncio.Task] = None

    # ── Properties ─────────────────────────────────────────────────────────

    @property
    def window_id(self) -> str:
        return self._window_id

    @property
    def target_btc(self) -> float:
        return self._target_btc

    def time_left(self) -> int:
        """Seconds remaining in the current window."""
        now = int(datetime.now(timezone.utc).timestamp())
        return max(0, self._end_ts - now)

    # ── Connect ────────────────────────────────────────────────────────────

    async def connect(self) -> bool:
        """
        Discover market, fetch target price, and open WebSocket connections.
        Returns True on success.
        """
        # 1. Discover market
        print("[feed] Discovering BTC 5-min market...")
        self._market = self._gamma.get_current_5m_btc_market()
        if not self._market:
            logger.error("No active BTC 5-min market found")
            return False

        # Parse token IDs and window metadata
        token_ids = self._gamma.parse_token_ids(self._market)
        self._up_token   = token_ids.get("up", "")
        self._down_token = token_ids.get("down", "")
        self._window_id  = self._market.get("slug", "")
        self._end_ts     = _parse_end_ts(self._market)
        prices           = self._gamma.parse_prices(self._market)
        self._up_price   = prices.get("up", 0.5)
        self._down_price = prices.get("down", 0.5)

        print(f"[feed] Market: {self._window_id} | ends in {self.time_left()}s")
        print(f"[feed] Tokens: up={self._up_token[:8]}… down={self._down_token[:8]}…")

        # 2. Fetch target price from Vatic API
        self._target_btc = await _fetch_vatic_target(self._end_ts - 300)
        print(f"[feed] Target BTC: ${self._target_btc:,.2f}")

        # 3. Start live BTC price WebSocket (background task)
        self._live_btc_task = asyncio.create_task(
            self._stream_live_btc(), name="live_btc_feed"
        )

        # 4. Hook orderbook WebSocket
        @self._ws.on_book
        async def _on_book(snapshot: OrderbookSnapshot):
            if snapshot.asset_id == self._up_token:
                self._up_price = snapshot.mid_price
            elif snapshot.asset_id == self._down_token:
                self._down_price = snapshot.mid_price
            await self._queue.put("tick")

        # 5. Subscribe to UP and DOWN tokens
        await self._ws.subscribe([self._up_token, self._down_token])
        asyncio.create_task(self._ws.run(), name="clob_ws")

        # Give WebSocket a moment to connect
        await asyncio.sleep(0.5)
        return True

    async def disconnect(self):
        """Cancel background tasks and close connections."""
        if self._live_btc_task:
            self._live_btc_task.cancel()
        await self._ws.disconnect()

    # ── Stream ─────────────────────────────────────────────────────────────

    async def stream(self) -> AsyncIterator[MarketState]:
        """
        Yield MarketState on each WebSocket orderbook update.
        Stops when time_left reaches 0.
        """
        while True:
            tl = self.time_left()
            if tl <= 0:
                break

            try:
                await asyncio.wait_for(self._queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                pass  # emit a tick anyway so strategy can react to stale prices

            spread = self._live_btc - self._target_btc

            yield MarketState(
                time_left  = self.time_left(),
                target_btc = self._target_btc,
                live_btc   = self._live_btc,
                spread     = spread,
                up_price   = self._up_price,
                down_price = self._down_price,
                window_id  = self._window_id,
                # position fields are injected by engine.py
            )

    # ── Internal ───────────────────────────────────────────────────────────

    async def _stream_live_btc(self):
        """Subscribe to Polymarket's live BTC price feed (crypto_prices_chainlink)."""
        try:
            import websockets as ws_lib
            req = {
                "action": "subscribe",
                "subscriptions": [{"topic": "crypto_prices_chainlink", "type": "*"}],
            }
            while True:
                try:
                    async with ws_lib.connect(LIVE_BTC_WSS) as ws:
                        await ws.send(json.dumps(req))
                        async for raw in ws:
                            if "crypto_prices_chainlink" in raw and '"btc/usd"' in raw:
                                data = json.loads(raw)
                                self._live_btc = float(data["payload"]["value"])
                except asyncio.CancelledError:
                    return
                except Exception as exc:
                    logger.debug(f"live_btc ws error: {exc}, reconnecting…")
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass


# ── Helpers ────────────────────────────────────────────────────────────────

def _parse_end_ts(market: dict) -> int:
    """Parse market end timestamp from endDate ISO string."""
    end_date = market.get("endDate", "")
    if end_date:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            return int(dt.timestamp())
        except Exception:
            pass
    # Fallback: now + 300s
    return int(datetime.now(timezone.utc).timestamp()) + 300


async def _fetch_vatic_target(start_ts: int) -> float:
    """Poll Vatic API until target price is returned. Non-blocking via executor."""
    url = VATIC_URL.format(ts=start_ts)
    loop = asyncio.get_running_loop()

    for attempt in range(10):
        try:
            resp = await loop.run_in_executor(
                None, lambda: requests.get(url, timeout=5)
            )
            if resp.status_code == 200:
                data = resp.json()
                target = (
                    data.get("strike")
                    or data.get("price")
                    or data.get("target")
                    or data.get("value")
                )
                if target is not None:
                    return float(target)
        except Exception as exc:
            logger.debug(f"Vatic attempt {attempt}: {exc}")
        await asyncio.sleep(2)

    logger.warning("Vatic API did not return target price. Defaulting to 0.0")
    return 0.0
