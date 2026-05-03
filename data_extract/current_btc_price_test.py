"""
Just return the current BTC price from Polymarket's websocket feed.
Just for testing
"""

import asyncio, json, websockets

async def get_btc_oneshot():
    async with websockets.connect("wss://ws-live-data.polymarket.com") as ws:
        await ws.send(json.dumps({"action": "subscribe", "subscriptions": [{"topic": "crypto_prices_chainlink", "type": "*"}]}))
        while True:
            raw_msg = await ws.recv()
            # Check the raw string first before attempting to parse JSON
            if "crypto_prices_chainlink" in raw_msg and '"btc/usd"' in raw_msg:
                msg = json.loads(raw_msg)
                print(msg["payload"]["value"])


asyncio.run(get_btc_oneshot())