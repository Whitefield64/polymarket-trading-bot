"""
Just return the current mid price of the up and down tokens for the currently active 5-min BTC market, along with the time left until the market ends.
Just for testing
"""

import sys
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
logging.getLogger("src.websocket_client").setLevel(logging.WARNING)
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.gamma_client import GammaClient
from src.websocket_client import MarketWebSocket

def find_active_5m_market() -> dict | None:
    """Return the currently active 5-min BTC market from the Gamma API."""
    client = GammaClient()
    now = datetime.now(timezone.utc)
    minute = (now.minute // 5) * 5
    window = now.replace(minute=minute, second=0, microsecond=0)
    base_ts = int(window.timestamp())

    for offset in [0, 300, -300]:
        slug = f"btc-updown-5m-{base_ts + offset}"
        market = client.get_market_by_slug(slug)
        if market and market.get("acceptingOrders"):
            return market
    return None

async def main() -> None:
    market = find_active_5m_market()
    if not market:
        print("No active 5-min BTC market found. Try again in a moment.")
        return

    end_dt = datetime.fromisoformat(market["endDate"].replace("Z", "+00:00"))
    gc = GammaClient()
    token_ids = gc.parse_token_ids(market)

    state = {
        "up": 0.0,
        "down": 0.0,
    }

    ws = MarketWebSocket()

    @ws.on_book
    async def on_book(snap):
        if snap.asset_id == token_ids.get("up"):
            state["up"] = snap.mid_price
        elif snap.asset_id == token_ids.get("down"):
            state["down"] = snap.mid_price

    await ws.subscribe(list(token_ids.values()), replace=True)
    ws_task = asyncio.create_task(ws.run(auto_reconnect=True))

    print(f"{'time_left_s':<14} {'up_price':<14} {'down_price':<14}")
    print("-" * 45)

    try:
        while True:
            now = datetime.now(timezone.utc)
            time_left = max(0, int((end_dt - now).total_seconds()))

            print(f"{time_left:<14} {state['up']:<14.4f} {state['down']:<14.4f}")

            if time_left == 0:
                print("\nMarket ended.")
                break

            await asyncio.sleep(1.0)
    except KeyboardInterrupt:
        print("\nStopped early.")
    finally:
        ws_task.cancel()

if __name__ == "__main__":
    asyncio.run(main())