"""Binance spot testnet (crypto). Docs: https://developers.binance.com/docs/binance-spot-api-docs

Spot trading is long-only. The testnet pre-loads balances of many coins; the agent only
counts coins it bought itself as positions, and values the account as USDT + those positions.
Binance spot has no attached stop-loss here, so stops are checked when the agent runs.
"""
from __future__ import annotations

import hashlib
import hmac
import math
import time
from urllib.parse import urlencode

from .base import BrokerError, LiveBroker, require_keys

BASE = "https://testnet.binance.vision"  # testnet only; the live URL is deliberately not here
QUOTE = "USDT"


class BinanceTestnet(LiveBroker):
    name = "binance"

    def __init__(self, market: str, session=None):
        super().__init__(market, session)
        self.key, self.secret = require_keys("BINANCE_TESTNET_KEY", "BINANCE_TESTNET_SECRET")
        self.http.headers.update({"X-MBX-APIKEY": self.key})
        self._filters: dict[str, dict] = {}

    @staticmethod
    def pair(symbol: str) -> tuple[str, str]:
        """'BTC-USD' -> ('BTCUSDT', 'BTC'); USDT stands in for USD."""
        base = symbol.split("-")[0].upper()
        return base + QUOTE, base

    def _signed(self, method: str, path: str, params: dict | None = None) -> dict:
        params = dict(params or {}, timestamp=int(time.time() * 1000), recvWindow=5000)
        query = urlencode(params)
        sig = hmac.new(self.secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        r = self.http.request(method, f"{BASE}{path}?{query}&signature={sig}", timeout=20)
        return self._check(r, f"{method} {path}")

    def _balances(self) -> dict[str, float]:
        acct = self._signed("GET", "/api/v3/account")
        return {b["asset"]: float(b["free"]) + float(b["locked"]) for b in acct["balances"]}

    def _account(self) -> dict:
        return {"cash": self._balances().get(QUOTE, 0.0), "equity": None, "day_start": None}

    def _broker_positions(self) -> dict[str, tuple[float, float]]:
        balances = self._balances()
        out = {}
        for sym, j in self.journal["positions"].items():
            held = min(balances.get(self.pair(sym)[1], 0.0), abs(j["qty"]))
            if held > 0 and held * j["entry"] >= self._lot(sym)["min_notional"]:
                out[sym] = (held, j["entry"])
        return out

    def _lot(self, symbol: str) -> dict:
        pair = self.pair(symbol)[0]
        if pair not in self._filters:
            r = self.http.get(f"{BASE}/api/v3/exchangeInfo", params={"symbol": pair}, timeout=20)
            info = self._check(r, "exchangeInfo")["symbols"][0]
            f = {x["filterType"]: x for x in info["filters"]}
            notional = f.get("NOTIONAL") or f.get("MIN_NOTIONAL") or {}
            self._filters[pair] = {
                "step": float(f["LOT_SIZE"]["stepSize"]), "min_qty": float(f["LOT_SIZE"]["minQty"]),
                "min_notional": float(notional.get("minNotional", 0)),
            }
        return self._filters[pair]

    def round_qty(self, symbol: str, qty: float) -> float:
        lot = self._lot(symbol)
        q = math.floor(qty / lot["step"]) * lot["step"]
        return round(q, 8) if q >= lot["min_qty"] else 0.0

    def _order(self, symbol: str, side: str, qty: float, cid: str | None = None) -> tuple[float, float]:
        q = self.round_qty(symbol, qty)
        if q <= 0:
            raise BrokerError(f"{symbol}: quantity {qty:g} is below Binance's minimum")
        resp = self._signed("POST", "/api/v3/order", {
            "symbol": self.pair(symbol)[0], "side": side, "type": "MARKET", "quantity": f"{q:.8f}".rstrip("0").rstrip("."),
            **({"newClientOrderId": cid} if cid else {}),
        })
        filled = float(resp.get("executedQty", 0))
        quote_spent = float(resp.get("cummulativeQuoteQty", 0))  # Binance's spelling
        return filled, (quote_spent / filled if filled else 0.0)

    def _submit(self, symbol, side, qty, stop, target=None, cid=None):  # stop/target checked by the app
        if side < 0:
            raise BrokerError("Binance spot cannot sell short")
        filled, price = self._order(symbol, "BUY", qty, cid)
        return filled, price, None

    def _order_status(self, cid: str) -> str | None:
        sym = next((s for s, j in self.journal["positions"].items() if j.get("cid") == cid), None)
        if sym is None:
            return None
        try:
            o = self._signed("GET", "/api/v3/order", {"symbol": self.pair(sym)[0], "origClientOrderId": cid})
        except BrokerError as e:
            return "gone" if "-2013" in str(e) else None  # -2013: order does not exist
        return {"FILLED": "filled", "PARTIALLY_FILLED": "filled", "NEW": "open"}.get(o.get("status"), "gone")

    def _close(self, symbol, qty, part=None):
        held = self._balances().get(self.pair(symbol)[1], 0.0)
        return self._order(symbol, "SELL", min(abs(qty) if part is None else part, held))[1]

    def equity(self, prices, fxs=None):
        return self.cash + sum(p["qty"] * prices.get(s, p["entry"]) for s, p in self.positions.items())
