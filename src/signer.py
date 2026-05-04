"""
Signer Module - EIP-712 Order Signing

Provides EIP-712 signature functionality for Polymarket orders
and authentication messages.

EIP-712 is a standard for structured data hashing and signing
that provides better security and user experience than plain
message signing.

Example:
    from src.signer import OrderSigner

    signer = OrderSigner(private_key)
    signature = signer.sign_order(
        token_id="123...",
        price=0.65,
        size=10,
        side="BUY",
        maker="0x..."
    )
"""

import time
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Dict, Any
from dataclasses import dataclass
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import to_checksum_address


# pUSD has 6 decimal places (same as USDC)
USDC_DECIMALS = 6

# Zero bytes32 for optional V2 fields
BYTES32_ZERO = "0x" + "00" * 32


@dataclass
class Order:
    """
    Represents a Polymarket V2 order.

    Attributes:
        token_id: The ERC-1155 token ID for the market outcome
        price: Price per share (0-1, e.g., 0.65 = 65%)
        size: Number of shares
        side: Order side ('BUY' or 'SELL')
        maker: The maker's wallet address (Safe/Proxy)
        signature_type: Signature type (2 = Gnosis Safe)
        timestamp: Order creation time in milliseconds (defaults to now)
        metadata: Optional bytes32 metadata (hex string)
        builder: Optional bytes32 builder identifier (hex string)
        expiration: Order expiration (wire-only, not signed; "0" = no expiry)
    """
    token_id: str
    price: float
    size: float
    side: str
    maker: str
    signature_type: int = 2
    timestamp: Optional[int] = None
    metadata: str = BYTES32_ZERO
    builder: str = BYTES32_ZERO
    expiration: str = "0"

    def __post_init__(self):
        """Validate and normalize order parameters."""
        self.side = self.side.upper()
        if self.side not in ("BUY", "SELL"):
            raise ValueError(f"Invalid side: {self.side}")

        if not 0 < self.price <= 1:
            raise ValueError(f"Invalid price: {self.price}")

        if self.size <= 0:
            raise ValueError(f"Invalid size: {self.size}")

        if self.timestamp is None:
            self.timestamp = int(time.time() * 1000)  # milliseconds

        # Compute tick-aligned amounts matching the official py-clob-client:
        # 1. Round size to 2 decimal places first.
        # 2. BUY:  makerAmount = round(size_r * price, 4) * 10^6
        #          takerAmount = size_r * 10^6
        #    SELL: makerAmount = size_r * 10^6
        #          takerAmount = round(size_r * price, 4) * 10^6
        # Using Decimal throughout to avoid float precision issues.
        def _to_units(val_dec: Decimal) -> int:
            return int(val_dec * Decimal(10**USDC_DECIMALS))

        size_r = Decimal(str(self.size)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        price_d = Decimal(str(self.price))
        notional_r = (size_r * price_d).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

        if self.side == "BUY":
            self.maker_amount = str(_to_units(notional_r))
            self.taker_amount = str(_to_units(size_r))
        else:
            self.maker_amount = str(_to_units(size_r))
            self.taker_amount = str(_to_units(notional_r))

        self.side_value = 0 if self.side == "BUY" else 1


class SignerError(Exception):
    """Base exception for signer operations."""
    pass


class OrderSigner:
    """
    Signs Polymarket orders using EIP-712.

    This signer handles:
    - Authentication messages (L1)
    - Order messages (for CLOB submission)

    Attributes:
        wallet: The Ethereum wallet instance
        address: The signer's address
        domain: EIP-712 domain separator
    """

    # EIP-712 domain for L1 authentication (derive/create API key)
    AUTH_DOMAIN = {
        "name": "ClobAuthDomain",
        "version": "1",
        "chainId": 137,
    }

    # EIP-712 domain for order signing — CTF Exchange V2 (deployed April 28 2026)
    CTF_EXCHANGE_ADDRESS = "0xE111180000d2663C0091e4f400237545B87B996B"
    ORDER_DOMAIN = {
        "name": "Polymarket CTF Exchange",
        "version": "2",
        "chainId": 137,
        "verifyingContract": CTF_EXCHANGE_ADDRESS,
    }

    # Keep DOMAIN as alias for auth (backwards compat with sign_auth_message)
    DOMAIN = AUTH_DOMAIN

    # V2 order type definition for EIP-712
    # Removed vs V1: taker, expiration, nonce, feeRateBps
    # Added vs V1: timestamp, metadata, builder
    ORDER_TYPES = {
        "Order": [
            {"name": "salt", "type": "uint256"},
            {"name": "maker", "type": "address"},
            {"name": "signer", "type": "address"},
            {"name": "tokenId", "type": "uint256"},
            {"name": "makerAmount", "type": "uint256"},
            {"name": "takerAmount", "type": "uint256"},
            {"name": "side", "type": "uint8"},
            {"name": "signatureType", "type": "uint8"},
            {"name": "timestamp", "type": "uint256"},
            {"name": "metadata", "type": "bytes32"},
            {"name": "builder", "type": "bytes32"},
        ]
    }

    def __init__(self, private_key: str):
        """
        Initialize signer with a private key.

        Args:
            private_key: Private key (with or without 0x prefix)

        Raises:
            ValueError: If private key is invalid
        """
        if private_key.startswith("0x"):
            private_key = private_key[2:]

        try:
            self.wallet = Account.from_key(f"0x{private_key}")
        except Exception as e:
            raise ValueError(f"Invalid private key: {e}")

        self.address = self.wallet.address

    def sign_auth_message(
        self,
        timestamp: Optional[str] = None,
        nonce: int = 0
    ) -> str:
        """
        Sign an authentication message for L1 authentication.

        This signature is used to create or derive API credentials.

        Args:
            timestamp: Message timestamp (defaults to current time)
            nonce: Message nonce (usually 0)

        Returns:
            Hex-encoded signature
        """
        if timestamp is None:
            timestamp = str(int(time.time()))

        # Auth message types
        auth_types = {
            "ClobAuth": [
                {"name": "address", "type": "address"},
                {"name": "timestamp", "type": "string"},
                {"name": "nonce", "type": "uint256"},
                {"name": "message", "type": "string"},
            ]
        }

        message_data = {
            "address": self.address,
            "timestamp": timestamp,
            "nonce": nonce,
            "message": "This message attests that I control the given wallet",
        }

        signable = encode_typed_data(
            domain_data=self.DOMAIN,
            message_types=auth_types,
            message_data=message_data
        )

        signed = self.wallet.sign_message(signable)
        return "0x" + signed.signature.hex()

    def sign_order(self, order: Order) -> Dict[str, Any]:
        """
        Sign a Polymarket order.

        Args:
            order: Order instance to sign

        Returns:
            Dictionary containing order and signature

        Raises:
            SignerError: If signing fails
        """
        try:
            import random
            salt = random.randint(1, 2**32)
            maker = to_checksum_address(order.maker)

            def _hex_to_bytes32(hex_str: str) -> bytes:
                return bytes.fromhex(hex_str.replace("0x", "").zfill(64))

            # V2 signed struct: no taker/expiration/nonce/feeRateBps
            # New fields: timestamp (ms), metadata (bytes32), builder (bytes32)
            typed_data = {
                "primaryType": "Order",
                "types": {
                    "EIP712Domain": [
                        {"name": "name", "type": "string"},
                        {"name": "version", "type": "string"},
                        {"name": "chainId", "type": "uint256"},
                        {"name": "verifyingContract", "type": "address"},
                    ],
                    "Order": self.ORDER_TYPES["Order"],
                },
                "domain": self.ORDER_DOMAIN,
                "message": {
                    "salt": salt,
                    "maker": maker,
                    "signer": self.address,
                    "tokenId": int(order.token_id),
                    "makerAmount": int(order.maker_amount),
                    "takerAmount": int(order.taker_amount),
                    "side": order.side_value,
                    "signatureType": order.signature_type,
                    "timestamp": order.timestamp,
                    "metadata": _hex_to_bytes32(order.metadata),
                    "builder": _hex_to_bytes32(order.builder),
                },
            }

            signable = encode_typed_data(full_message=typed_data)
            signed = self.wallet.sign_message(signable)
            signature = "0x" + signed.signature.hex()

            # V2 wire body: expiration is present but NOT part of the signed struct
            return {
                "order": {
                    "salt": salt,
                    "maker": maker,
                    "signer": self.address,
                    "tokenId": order.token_id,
                    "makerAmount": order.maker_amount,
                    "takerAmount": order.taker_amount,
                    "side": order.side,
                    "expiration": order.expiration,
                    "signatureType": order.signature_type,
                    "timestamp": str(order.timestamp),
                    "metadata": order.metadata,
                    "builder": order.builder,
                    "signature": signature,
                },
            }

        except Exception as e:
            raise SignerError(f"Failed to sign order: {e}")

