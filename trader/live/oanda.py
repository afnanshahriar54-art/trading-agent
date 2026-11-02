"""OANDA practice account (forex). Docs: https://developer.oanda.com/rest-live-v20/introduction/"""
from __future__ import annotations

from .. import fx
from .base import BrokerError, LiveBroker, require_keys

BASE = "https://api-fxpractice.oanda.com"  # practice only; the live URL is deliberately not here


class OandaPractice(LiveBroker):
    name = "oanda"
    margin = True
    broker_side_exits = True

    def __init__(self, market: str, session=None):
        super().__init__(market, session)
        token, self.account_id = require_keys("OANDA_TOKEN", "OANDA_ACCOUNT_ID")
        self.http.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})

    def _req(self, method: str, path: str, **kw) -> dict:
        url = f"{BASE}/v3/accounts/{self.account_id}{path}"
        return self._check(self.http.request(method, url, timeout=20, **kw), f"{method} {path}")

    @staticmethod
    def instrument(symbol: str) -> str:
        base, quote = fx.pair(symbol)
        return f"{base}_{quote}"

    @staticmethod
    def _price(symbol: str, price: float) -> str:
        return f"{price:.3f}" if "JPY" in symbol.upper() else f"{price:.5f}"

    def _account(self) -> dict:
        a = self._req("GET", "/summary")["account"]
        if a.get("currency") != fx.ACCOUNT_CCY:
            raise BrokerError(f"OANDA account currency is {a.get('currency')}; this agent expects {fx.ACCOUNT_CCY}")
        return {"cash": float(a["balance"]), "equity": float(a["NAV"]), "day_start": None}

    def _broker_positions(self) -> dict[str, tuple[float, float]]:
        out = {}
        for p in self._req("GET", "/openPositions")["positions"]:
            long_u, short_u = float(p["long"]["units"]), float(p["short"]["units"])
            qty = long_u + short_u
            if qty == 0:
                continue
            leg = p["long"] if qty > 0 else p["short"]
            out[p["instrument"].replace("_", "") + "=X"] = (qty, float(leg.get("averagePrice", 0)))
        return out

    def _submit(self, symbol, side, qty, stop, target=None):
        order = {
            "type": "MARKET", "instrument": self.instrument(symbol), "units": str(int(side * qty)),
            "timeInForce": "FOK", "positionFill": "DEFAULT",
            # Safety net and take-profit live at OANDA, so they work while the app is off.
            "stopLossOnFill": {"price": self._price(symbol, stop)},
        }
        if target:
            order["takeProfitOnFill"] = {"price": self._price(symbol, target)}
        resp = self._req("POST", "/orders", json={"order": order})
        if "orderCancelTransaction" in resp:
            raise BrokerError(f"OANDA cancelled the order: {resp['orderCancelTransaction'].get('reason')}")
        f = resp.get("orderFillTransaction", {})
        return abs(float(f.get("units", qty))), float(f.get("price", 0)), None

    def _move_stop(self, symbol, stop):
        """Change the stop-loss on the open OANDA trade(s) in this instrument."""
        for t in self._req("GET", "/openTrades")["trades"]:
            if t["instrument"] == self.instrument(symbol):
                self._req("PUT", f"/trades/{t['id']}/orders", json={"stopLoss": {"price": self._price(symbol, stop)}})

    def _set_exits(self, symbol, qty, stop, target):
        orders = {"stopLoss": {"price": self._price(symbol, stop)}}
        orders["takeProfit"] = {"price": self._price(symbol, target)} if target else None  # None cancels it
        for t in self._req("GET", "/openTrades")["trades"]:
            if t["instrument"] == self.instrument(symbol):
                self._req("PUT", f"/trades/{t['id']}/orders", json=orders)

    def _close(self, symbol, qty, part=None):
        units = "ALL" if part is None else str(int(part))
        body = {"longUnits": units} if qty > 0 else {"shortUnits": units}
        resp = self._req("PUT", f"/positions/{self.instrument(symbol)}/close", json=body)
        f = resp.get("longOrderFillTransaction") or resp.get("shortOrderFillTransaction") or {}
        return float(f.get("price", 0))
