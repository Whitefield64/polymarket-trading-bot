"""
Scraper for collecting 5-minute BTC Up/Down market data from Polymarket's Gamma platform, 
enriched with live BTC price and the official target price from the Vatic API.
Extract:
- Time left in the market
- Official target price (via Vatic API)
- Live BTC price (via Polymarket WebSocket)
- UP and DOWN mid-prices (via Polymarket WebSocket)
Saves each market's data as a CSV file in the datasets/ directory, with one row per second for the entire 5-minute session. 
Designed to run continuously and build a historical dataset over time.
"""

import sys
import asyncio
import json
import logging
import csv
import requests
import websockets
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# --- Local Imports ---
load_dotenv()
logging.getLogger("src.websocket_client").setLevel(logging.WARNING)
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.gamma_client import GammaClient
from src.websocket_client import MarketWebSocket

DATA_DIR = Path(__file__).parent.parent / "datasets"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# --- 1. Vatic API Target Scraper ---
async def wait_for_vatic_target(start_ts_sec: int) -> float:
    """Polls the 3rd-party Vatic API used by professional bots for the exact target price."""
    url = f"https://api.vatic.trading/api/v1/targets/timestamp?asset=btc&type=5min&timestamp={start_ts_sec}"
    print(f"[*] Polling Vatic Trading API for official target...")
    
    while True:
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: requests.get(url, timeout=5))
            
            if response.status_code == 200:
                data = response.json()
                
                # Check common JSON keys 
                target = data.get("strike") or data.get("price") or data.get("target") or data.get("value")
                
                if target is not None:
                    target_float = float(target)
                    print(f"[+] TARGET LOCKED (via Vatic API): ${target_float:,.2f}")
                    return target_float
                else:
                    print(f"[!] Vatic API returned unknown structure: {data}")
        except Exception as e:
            # Silently retry on connection drops, which is normal over 10 hours
            pass 
            
        await asyncio.sleep(2)

# --- 3. Live BTC WebSocket ---
async def stream_live_btc(state: dict):
    uri = "wss://ws-live-data.polymarket.com"
    req = {"action": "subscribe", "subscriptions": [{"topic": "crypto_prices_chainlink", "type": "*"}]}
    
    while True:
        try:
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps(req))
                while True:
                    raw_msg = await ws.recv()
                    if "crypto_prices_chainlink" in raw_msg and '"btc/usd"' in raw_msg:
                        msg = json.loads(raw_msg)
                        state["live_btc"] = float(msg["payload"]["value"])
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(1) # Auto-reconnect if Polymarket drops connection

# --- 4. The 5-Minute Session Runner (In-Memory Version) ---
async def record_market_session(market: dict, btc_state: dict):
    slug = market["slug"]
    start_ts_sec = int(slug.split('-')[-1])
    end_dt = datetime.fromisoformat(market["endDate"].replace("Z", "+00:00"))
    
    print(f"\n" + "="*60)
    print(f"🚀 STARTING NEW MARKET: {slug}")
    print("="*60)

    gc = GammaClient()
    token_ids = gc.parse_token_ids(market)
    book_state = {"up": 0.0, "down": 0.0}
    book_ws = MarketWebSocket()

    @book_ws.on_book
    async def on_book(snap):
        if snap.asset_id == token_ids.get("up"):
            book_state["up"] = snap.mid_price
        elif snap.asset_id == token_ids.get("down"):
            book_state["down"] = snap.mid_price

    await book_ws.subscribe(list(token_ids.values()), replace=True)
    book_task = asyncio.create_task(book_ws.run(auto_reconnect=True))

    target_btc = await wait_for_vatic_target(start_ts_sec)

    print(f"[*] Recording 1 row/sec into memory...")
    
    # 1. Setup our in-memory list and header
    header = ["Time Left", "Target BTC", "Live BTC", "Spread", "UP Price", "DOWN Price"]
    session_data = [header] 
        
    # 2. Main recording loop (Memory Only)
    while True:
        now = datetime.now(timezone.utc)
        time_left = max(0, int((end_dt - now).total_seconds()))
        
        live_price = btc_state.get("live_btc", 0.0)
        if live_price == 0.0:
            spread = 0.0
        else:
            spread = live_price - target_btc

        up_p = book_state.get("up", 0.0) if book_state.get("up") is not None else 0.0
        dn_p = book_state.get("down", 0.0) if book_state.get("down") is not None else 0.0

        row = [
            time_left,
            f"{target_btc:.2f}",
            f"{live_price:.2f}",
            spread,
            f"{up_p:.4f}",
            f"{dn_p:.4f}"
        ]
        
        # Append to memory instead of writing to disk
        session_data.append(row)
        
        print(f"T-{time_left:<3}s | Target: {target_btc:.2f} | Live: {live_price:.2f} | {spread:<4} | UP: {up_p:.4f} | DOWN: {dn_p:.4f}")

        if time_left <= 0:
            print(f"\n[!] Market {slug} ended.")
            break
            
        await asyncio.sleep(1.0)

    # 3. Market is over. Dump the entire memory block to the CSV file at once.
    filename = DATA_DIR / f"{slug}.csv"
    print(f"[*] Saving batch data to disk: {filename.name}")
    try:
        with open(filename, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerows(session_data)
        print(f"[+] Saved successfully.")
    except Exception as e:
        print(f"[!] Failed to save CSV to disk: {e}")

    book_task.cancel()

# --- 5. Continuous Loop Manager ---
async def main():
    print("🤖 Dataset Builder Started. Press Ctrl+C to stop.\n")
    
    btc_state = {"live_btc": 0.0}
    btc_ws_task = asyncio.create_task(stream_live_btc(btc_state))
    
    try:
        while True:
            now = datetime.now(timezone.utc)
            minute = now.minute
            
            # Align with the 5-minute boundaries
            if minute % 5 != 0 and minute % 5 != 4:
                wait_min = 5 - (minute % 5)
                print(f"Started mid-market. Waiting {wait_min} minutes for the next clean window to open...")
                await asyncio.sleep(60 * wait_min)
                continue

            market = GammaClient().get_current_5m_btc_market()
            
            if market:
                await record_market_session(market, btc_state)
                await asyncio.sleep(2)
            else:
                await asyncio.sleep(5)
                
    except KeyboardInterrupt:
        print("\n[!] Dataset Builder stopped by user. All files saved.")
    finally:
        btc_ws_task.cancel()

if __name__ == "__main__":
    asyncio.run(main())