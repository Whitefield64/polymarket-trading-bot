"""
Diagnostic script — verify what's actually on-chain / in CLOB for your account.

Usage:
    uv run python check_orders.py
    uv run python check_orders.py --order 0x9114260a...   # inspect specific order
"""

import argparse
import json
from src.config import Config
from src.signer import OrderSigner
from src.client import ClobClient


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--order", help="Specific order ID to inspect")
    args = parser.parse_args()

    config = Config.from_env()
    import os
    private_key = os.environ.get("POLY_PRIVATE_KEY", "")
    if not private_key:
        print("ERROR: POLY_PRIVATE_KEY not set in .env")
        return

    signer = OrderSigner(private_key)

    client = ClobClient(
        host=config.clob.host,
        chain_id=config.clob.chain_id,
        signature_type=config.clob.signature_type,
        funder=config.safe_address,
        builder_creds=config.builder if config.use_gasless else None,
    )

    print(f"Signer (MetaMask) address : {signer.address}")
    print(f"Safe (proxy) address      : {config.safe_address}")
    print()

    # Derive API creds
    try:
        creds = client.create_or_derive_api_key(signer)
        client.set_api_creds(creds, signing_address=signer.address)
        print(f"API key derived: {creds.api_key}")
    except Exception as e:
        print(f"ERROR deriving API key: {e}")
        return

    print()

    # Inspect a specific order
    if args.order:
        print(f"=== Order {args.order} ===")
        try:
            order = client.get_order(args.order)
            print(json.dumps(order, indent=2))
        except Exception as e:
            print(f"ERROR: {e}")
        print()

    # Open orders
    print("=== Open Orders ===")
    try:
        orders = client.get_open_orders()
        if orders:
            for o in orders:
                print(f"  {o.get('id', o.get('orderID', '?'))} | "
                      f"side={o.get('side')} | "
                      f"price={o.get('price')} | "
                      f"size={o.get('size')} | "
                      f"status={o.get('status')}")
        else:
            print("  (none)")
    except Exception as e:
        print(f"  ERROR: {e}")

    print()

    # Recent trades
    print("=== Recent Trades (last 20) ===")
    try:
        trades = client.get_trades(limit=20)
        if trades:
            for t in trades:
                print(f"  {t.get('id', '?')} | "
                      f"side={t.get('side')} | "
                      f"price={t.get('price')} | "
                      f"size={t.get('size')} | "
                      f"status={t.get('status')} | "
                      f"matched_at={t.get('matched_at', t.get('timestamp', '?'))}")
        else:
            print("  (none — no fills recorded)")
    except Exception as e:
        print(f"  ERROR: {e}")


if __name__ == "__main__":
    main()
