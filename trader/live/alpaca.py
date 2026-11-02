"""Alpaca paper trading (US stocks/ETFs). Docs: https://docs.alpaca.markets/reference"""
from __future__ import annotations

import time

from .base import BrokerError, LiveBroker, require_keys

BASE = "https://paper-api.alpaca.markets"  # paper only; the live URL is deliberately not here
DATA = "https://data.alpaca.markets"       # market data (prices only, no trading)


def latest_trade(symbol: str) -> tuple[float, str]:
    """(price, ISO time) of the latest trade from Alpaca's free real-time IEX feed."""
    import requests

    key, secret = require_keys("ALPACA_API_KEY", "ALPACA_SECRET_KEY")
    r = requests.get(f"{DATA}/v2/stocks/{symbol.replace('-', '.')}/trades/latest", params={"feed": "iex"},
                     headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}, timeout=5)
    if r.status_code >= 400:
        raise BrokerError(f"Alpaca price: HTTP {r.status_code}")
    t = r.json()["trade"]
    return float(t["p"]), t["t"]


def stock_bars(symbol: str, timeframe: str, start: str) -> list[dict]:
    """Price bars from Alpaca's free real-time IEX feed. Raises BrokerError without keys."""
    import requests

    key, secret = require_keys("ALPACA_API_KEY", "ALPACA_SECRET_KEY")
    bars, token = [], None
    while True:
        params = {"timeframe": timeframe, "start": start, "limit": 10000, "feed": "iex", "adjustment": "raw"}
        if token:
            params["page_token"] = token
        r = requests.get(f"{DATA}/v2/stocks/{symbol.replace('-', '.')}/bars", params=params, timeout=15,
                         headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
        if r.status_code >= 400:
            raise BrokerError(f"Alpaca prices: HTTP {r.status_code} {r.text[:200]}")
        body = r.json()
        bars += body.get("bars") or []
        token = body.get("next_page_token")
        if not token:
            return bars


class AlpacaPaper(LiveBroker):
    name = "alpaca"
    broker_side_exits = True

    def __init__(self, market: str, session=None):
        super().__init__(market, session)
        key, secret = require_keys("ALPACA_API_KEY", "ALPACA_SECRET_KEY")
        self.http.headers.update({"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})

    def _req(self, method: str, path: str, **kw) -> dict | list:
        return self._check(self.http.request(method, BASE + path, timeout=20, **kw), f"{method} {path}")

    @staticmethod
    def _sym(symbol: str) -> str:
        return symbol.replace("-", ".")  # Yahoo BRK-B -> Alpaca BRK.B

    def _account(self) -> dict:
        a = self._req("GET", "/v2/account")
        if a.get("trading_blocked") or a.get("account_blocked"):
            raise BrokerError("Alpaca account is blocked from trading")
        # last_equity = equity at the previous close, the natural base for the daily loss limit
        return {"cash": float(a["cash"]), "equity": float(a["equity"]), "day_start": float(a["last_equity"])}

    def _broker_positions(self) -> dict[str, tuple[float, float]]:
        out = {}
        for p in self._req("GET", "/v2/positions"):
            qty = abs(float(p["qty"])) * (1 if p["side"] == "long" else -1)
            out[p["symbol"].replace(".", "-")] = (qty, float(p["avg_entry_price"]))
        return out

    def _submit(self, symbol, side, qty, stop, target=None):
        if not self.whole(qty):
            if side < 0:
                raise BrokerError("Alpaca can't sell short part of a share. Use a whole number of shares.")
            # Part of a share: a plain market order for today. The app itself watches its safety net
            # and take-profit (every 30 seconds while open, and at the daily check).
            order = self._req("POST", "/v2/orders", json={
                "symbol": self._sym(symbol), "qty": f"{qty:.6f}".rstrip("0").rstrip("."),
                "side": "buy", "type": "market", "time_in_force": "day"})
            return self._wait(order)
        body = {
            "symbol": self._sym(symbol), "qty": str(int(qty)),
            "side": "buy" if side > 0 else "sell", "type": "market", "time_in_force": "gtc",
            # The exit orders are placed at Alpaca as soon as the entry fills, so they work
            # instantly even while this app is closed. Bracket = safety net + take-profit.
            "order_class": "oto", "stop_loss": {"stop_price": f"{stop:.2f}"},
        }
        if target:
            body["order_class"] = "bracket"
            body["take_profit"] = {"limit_price": f"{target:.2f}"}
        return self._wait(self._req("POST", "/v2/orders", json=body))

    def _wait(self, order: dict):
        for _ in range(5):  # market orders fill within seconds while the market is open
            if order["status"] == "filled":
                return float(order["filled_qty"]), float(order["filled_avg_price"]), None
            if order["status"] in ("canceled", "expired", "rejected"):
                raise BrokerError(f"Alpaca order {order['status']}")
            time.sleep(1)
            order = self._req("GET", f"/v2/orders/{order['id']}")
        return 0.0, 0.0, order["id"]  # market closed: fills at the next open

    def _move_stop(self, symbol, stop):
        """Change the price of the resting safety-net (stop) order at Alpaca."""
        for o in self._req("GET", "/v2/orders", params={"status": "open", "symbols": self._sym(symbol), "nested": "true"}):
            for leg in [o, *(o.get("legs") or [])]:
                if leg.get("type") in ("stop", "stop_limit") and leg.get("status") in ("new", "accepted", "held", "pending_replace"):
                    self._req("PATCH", f"/v2/orders/{leg['id']}", json={"stop_price": f"{stop:.2f}"})
                    return

    def _order_open(self, order_id: str) -> bool:
        return self._req("GET", f"/v2/orders/{order_id}")["status"] not in (
            "filled", "canceled", "expired", "rejected", "done_for_day")

    def _cancel_exits(self, sym: str) -> None:
        # Exit orders reserve the shares, so they must be cancelled before closing or replacing.
        for o in self._req("GET", "/v2/orders", params={"status": "open", "symbols": sym}):
            self._req("DELETE", f"/v2/orders/{o['id']}")

    def _place_exits(self, symbol: str, qty: float, stop: float, target: float | None) -> None:
        """Place the safety net (and take-profit, as a one-cancels-other pair) for `qty` (signed) shares."""
        body = {"symbol": self._sym(symbol), "qty": str(int(abs(qty))), "side": "sell" if qty > 0 else "buy",
                "time_in_force": "gtc"}
        if target:
            body.update(type="limit", order_class="oco", take_profit={"limit_price": f"{target:.2f}"},
                        stop_loss={"stop_price": f"{stop:.2f}"})
        else:
            body.update(type="stop", stop_price=f"{stop:.2f}")
        self._req("POST", "/v2/orders", json=body)

    def _close(self, symbol, qty, part=None):
        sym = self._sym(symbol)
        self._cancel_exits(sym)
        if part is None:
            order = self._req("DELETE", f"/v2/positions/{sym}")
        else:
            order = self._req("DELETE", f"/v2/positions/{sym}", params={"qty": f"{part:.6f}".rstrip("0").rstrip(".")})
            j = self.journal["positions"].get(symbol)
            remaining = qty - (1 if qty > 0 else -1) * part
            if j and self.whole(remaining) and round(remaining):  # keep the rest protected at Alpaca
                j.pop("app_exits", None)
                self._place_exits(symbol, round(remaining), j["stop"], j.get("target"))
            elif j and remaining:  # part of a share left: the app watches it
                j["app_exits"] = True
        return float(order.get("filled_avg_price") or 0)

    def _set_exits(self, symbol, qty, stop, target):  # only called while Alpaca holds the exits
        self._cancel_exits(self._sym(symbol))
        self._place_exits(symbol, qty, stop, target)
