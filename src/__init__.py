"""
Polymarket Trading Bot — core library.

Quick start:
    from src import TradingBot, Config
    config = Config(safe_address="0x...")
    bot = TradingBot(config=config, private_key="0x...")
    result = await bot.place_order(token_id, price=0.5, size=1.0, side="BUY")
"""

from .bot import TradingBot, OrderResult
from .signer import OrderSigner, Order
from .client import ApiClient, ClobClient, RelayerClient
from .config import Config, BuilderConfig
from .gamma_client import GammaClient
from .websocket_client import MarketWebSocket, OrderbookSnapshot
from .utils import create_bot_from_env

__version__ = "1.0.0"

__all__ = [
    "TradingBot",
    "OrderResult",
    "OrderSigner",
    "Order",
    "ApiClient",
    "ClobClient",
    "RelayerClient",
    "Config",
    "BuilderConfig",
    "GammaClient",
    "MarketWebSocket",
    "OrderbookSnapshot",
    "create_bot_from_env",
]
