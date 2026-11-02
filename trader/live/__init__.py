"""Broker adapters for practice accounts: Alpaca paper, OANDA practice, Binance spot testnet.

Only the practice/test endpoints are wired in. Real-money endpoints are deliberately absent.
"""
from .alpaca import AlpacaPaper
from .base import BrokerError, LiveBroker
from .binance import BinanceTestnet
from .oanda import OandaPractice

BROKERS: dict[str, type[LiveBroker]] = {
    "alpaca": AlpacaPaper,
    "oanda": OandaPractice,
    "binance": BinanceTestnet,
}

__all__ = ["BROKERS", "BrokerError", "LiveBroker"]
