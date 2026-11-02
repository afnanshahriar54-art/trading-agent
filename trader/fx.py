"""Currency conversion for forex pairs.

A forex position of `qty` units of the base currency gains or loses
`qty * price change` in the QUOTE currency. The paper account is in USD, so
that amount is multiplied by `quote_to_usd(...)`:

    EURUSD (quote USD)  -> 1
    USDJPY (quote JPY)  -> 1 / USDJPY price
    EURGBP (cross)      -> GBPUSD price, looked up from market data
"""
from __future__ import annotations

from . import data

ACCOUNT_CCY = "USD"


def pair(symbol: str) -> tuple[str, str]:
    """'USDJPY=X' -> ('USD', 'JPY')."""
    code = symbol.replace("=X", "").replace("/", "").upper()
    if len(code) != 6:
        raise ValueError(f"{symbol}: not a 6-letter currency pair")
    return code[:3], code[3:]


def quote_to_usd(symbol: str, price: float, years: int = 1) -> float:
    base, quote = pair(symbol)
    if quote == ACCOUNT_CCY:
        return 1.0
    if base == ACCOUNT_CCY:
        return 1.0 / price
    try:  # cross pair: need the quote currency's own USD rate
        return float(data.load(f"{quote}USD=X", "forex", years).close.iloc[-1])
    except Exception:
        return 1.0 / float(data.load(f"USD{quote}=X", "forex", years).close.iloc[-1])
