"""
Trading Bot Module - Main Trading Interface

A production-ready trading bot for Polymarket with:
- Gasless transactions via Builder Program
- Encrypted private key storage
- Modular strategy support
- Comprehensive order management

Example:
    from src.bot import TradingBot

    # Initialize with config
    bot = TradingBot(config_path="config.yaml")

    # Or manually
    bot = TradingBot(
        safe_address="0x...",
        builder_creds=builder_creds,
        private_key="0x..."  # or use encrypted key
    )

    # Place an order
    result = await bot.place_order(
        token_id="123...",
        price=0.65,
        size=10,
        side="BUY"
    )
"""

import os
import asyncio
import logging
from typing import Optional, Dict, Any, List, Callable, TypeVar
from dataclasses import dataclass, field

from .config import Config, BuilderConfig
from .signer import OrderSigner, Order, BYTES32_ZERO
from .client import ClobClient, RelayerClient, ApiCredentials


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class OrderResult:
    """Result of an order operation."""
    success: bool
    order_id: Optional[str] = None
    status: Optional[str] = None
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_response(cls, response: Dict[str, Any]) -> "OrderResult":
        """Create from API response."""
        success = response.get("success", False)
        error_msg = response.get("errorMsg", "")

        return cls(
            success=success,
            order_id=response.get("orderId"),
            status=response.get("status"),
            message=error_msg if not success else "Order placed successfully",
            data=response
        )


class TradingBotError(Exception):
    """Base exception for trading bot errors."""
    pass


class NotInitializedError(TradingBotError):
    """Raised when bot is not initialized."""
    pass


class TradingBot:
    """
    Main trading bot class for Polymarket.

    Provides a high-level interface for:
    - Order placement and cancellation
    - Position management
    - Trade history
    - Gasless transactions (with Builder Program)

    Attributes:
        config: Bot configuration
        signer: Order signer instance
        clob_client: CLOB API client
        relayer_client: Relayer API client (if gasless enabled)
    """

    def __init__(
        self,
        config_path: Optional[str] = None,
        config: Optional[Config] = None,
        safe_address: Optional[str] = None,
        builder_creds: Optional[BuilderConfig] = None,
        private_key: Optional[str] = None,
        api_creds_path: Optional[str] = None,
        log_level: int = logging.INFO
    ):
        """
        Initialize trading bot.

        Can be initialized in multiple ways:

        1. From config file:
           bot = TradingBot(config_path="config.yaml")

        2. From Config object:
           bot = TradingBot(config=my_config)

        3. With manual parameters:
           bot = TradingBot(
               safe_address="0x...",
               builder_creds=builder_creds,
               private_key="0x..."
           )

        4. With encrypted key:
           bot = TradingBot(
               safe_address="0x...",
               encrypted_key_path="credentials/key.enc",
               password="mypassword"
           )

        Args:
            config_path: Path to config YAML file
            config: Config object
            safe_address: Safe/Proxy wallet address
            builder_creds: Builder Program credentials
            private_key: Raw private key (with 0x prefix)
            encrypted_key_path: Path to encrypted key file
            password: Password for encrypted key
            api_creds_path: Path to API credentials file
            log_level: Logging level
        """
        # Set log level
        logger.setLevel(log_level)

        # Load configuration
        if config_path:
            self.config = Config.load(config_path)
        elif config:
            self.config = config
        else:
            self.config = Config()

        # Override with provided parameters
        if safe_address:
            self.config.safe_address = safe_address
        if builder_creds:
            self.config.builder = builder_creds
            self.config.use_gasless = True

        # Initialize components
        self.signer: Optional[OrderSigner] = None
        self.clob_client: Optional[ClobClient] = None
        self.relayer_client: Optional[RelayerClient] = None
        self._api_creds: Optional[ApiCredentials] = None

        # Load private key
        if private_key:
            self.signer = OrderSigner(private_key)

        # Load API credentials
        if api_creds_path:
            self._load_api_creds(api_creds_path)

        # Initialize API clients
        self._init_clients()

        # Auto-derive API credentials if we have a signer but no API creds
        if self.signer and not self._api_creds:
            self._derive_api_creds()

        logger.info(f"TradingBot initialized (gasless: {self.config.use_gasless})")

    def _load_api_creds(self, filepath: str) -> None:
        """Load API credentials from file."""
        if os.path.exists(filepath):
            try:
                self._api_creds = ApiCredentials.load(filepath)
                logger.info(f"Loaded API credentials from {filepath}")
            except Exception as e:
                logger.warning(f"Failed to load API credentials: {e}")

    def _derive_api_creds(self) -> None:
        """Derive L2 API credentials from signer."""
        if not self.signer or not self.clob_client:
            return

        try:
            logger.info("Deriving L2 API credentials...")
            self._api_creds = self.clob_client.create_or_derive_api_key(self.signer)
            self.clob_client.set_api_creds(self._api_creds, signing_address=self.signer.address)
            logger.info("L2 API credentials derived successfully")
        except Exception as e:
            logger.warning(f"Failed to derive API credentials: {e}")
            logger.warning("Some API endpoints may not be accessible")

    def _init_clients(self) -> None:
        """Initialize API clients."""
        # CLOB client
        self.clob_client = ClobClient(
            host=self.config.clob.host,
            chain_id=self.config.clob.chain_id,
            signature_type=self.config.clob.signature_type,
            funder=self.config.safe_address,
            api_creds=self._api_creds,
            builder_creds=self.config.builder if self.config.use_gasless else None,
        )

        # Relayer client (for gasless)
        if self.config.use_gasless:
            self.relayer_client = RelayerClient(
                host=self.config.relayer.host,
                chain_id=self.config.clob.chain_id,
                builder_creds=self.config.builder,
                tx_type=self.config.relayer.tx_type,
            )
            logger.info("Relayer client initialized (gasless enabled)")

    async def _run_in_thread(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Run a blocking call in a worker thread to avoid event loop stalls."""
        return await asyncio.to_thread(func, *args, **kwargs)

    def is_initialized(self) -> bool:
        """Check if bot is properly initialized."""
        return (
            self.signer is not None and
            self.config.safe_address and
            self.clob_client is not None
        )

    def require_signer(self) -> OrderSigner:
        """Get signer or raise if not initialized."""
        if not self.signer:
            raise NotInitializedError(
                "Signer not initialized. Provide private_key or encrypted_key."
            )
        return self.signer

    async def place_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,
        order_type: str = "GTC",
    ) -> OrderResult:
        """
        Place a limit order.

        Args:
            token_id: Market token ID
            price: Price per share (0-1)
            size: Number of shares
            side: 'BUY' or 'SELL'
            order_type: Order type (GTC, GTD, FOK)

        Returns:
            OrderResult with order status
        """
        signer = self.require_signer()

        try:
            # Create order
            order = Order(
                token_id=token_id,
                price=price,
                size=size,
                side=side,
                maker=self.config.safe_address,
                builder=self.config.builder.builder_code or BYTES32_ZERO,
            )

            # Sign order
            signed = signer.sign_order(order)

            # Submit to CLOB
            response = await self._run_in_thread(
                self.clob_client.post_order,
                signed,
                order_type,
            )

            logger.info(
                f"Order placed: {side} {size}@{price} "
                f"(token: {token_id[:16]}...)"
            )

            return OrderResult.from_response(response)

        except Exception as e:
            logger.error(f"Failed to place order: {e}")
            return OrderResult(
                success=False,
                message=str(e)
            )

    async def cancel_order(self, order_id: str) -> OrderResult:
        """
        Cancel a specific order.

        Args:
            order_id: Order ID to cancel

        Returns:
            OrderResult with cancellation status
        """
        try:
            response = await self._run_in_thread(self.clob_client.cancel_order, order_id)
            logger.info(f"Order cancelled: {order_id}")
            return OrderResult(
                success=True,
                order_id=order_id,
                message="Order cancelled",
                data=response
            )
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return OrderResult(
                success=False,
                order_id=order_id,
                message=str(e)
            )

    async def cancel_all_orders(self) -> OrderResult:
        """
        Cancel all open orders.

        Returns:
            OrderResult with cancellation status
        """
        try:
            response = await self._run_in_thread(self.clob_client.cancel_all_orders)
            logger.info("All orders cancelled")
            return OrderResult(
                success=True,
                message="All orders cancelled",
                data=response
            )
        except Exception as e:
            logger.error(f"Failed to cancel orders: {e}")
            return OrderResult(success=False, message=str(e))

    async def cancel_market_orders(
        self,
        market: Optional[str] = None,
        asset_id: Optional[str] = None
    ) -> OrderResult:
        """
        Cancel orders for a specific market.

        Args:
            market: Condition ID of the market (optional)
            asset_id: Token/asset ID (optional)

        Returns:
            OrderResult with cancellation status
        """
        try:
            response = await self._run_in_thread(
                self.clob_client.cancel_market_orders,
                market,
                asset_id,
            )
            logger.info(f"Market orders cancelled (market: {market or 'all'}, asset: {asset_id or 'all'})")
            return OrderResult(
                success=True,
                message=f"Orders cancelled for market {market or 'all'}",
                data=response
            )
        except Exception as e:
            logger.error(f"Failed to cancel market orders: {e}")
            return OrderResult(success=False, message=str(e))

