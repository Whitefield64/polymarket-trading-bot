"""
Utility helpers.
"""

from .config import Config, get_env
from .bot import TradingBot


def create_bot_from_env() -> TradingBot:
    """Create a TradingBot from environment variables (POLY_PRIVATE_KEY, POLY_SAFE_ADDRESS)."""
    private_key = get_env("PRIVATE_KEY")
    if not private_key:
        raise ValueError("POLY_PRIVATE_KEY environment variable is required.")

    safe_address = get_env("SAFE_ADDRESS")
    if not safe_address:
        raise ValueError("POLY_SAFE_ADDRESS environment variable is required.")

    return TradingBot(config=Config.from_env(), private_key=private_key)
