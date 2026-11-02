"""Offline tests for the broker adapters, using a fake HTTP session.

Run: python tests/test_brokers.py
Checks the requests each adapter sends and how it reads the replies. The reply shapes
follow each broker's API docs; a real practice-account run is still the final check.
"""
import hashlib
import hmac
import json
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import trader.live.alpaca as alpaca_mod
import trader.live.base as base
from trader.live import BrokerError
from trader.live.alpaca import AlpacaPaper
from trader.live.binance import BinanceTestnet
from trader.live.oanda import OandaPractice

tmp = Path(__file__).parent / ".brokertest"
shutil.rmtree(tmp, ignore_errors=True)
base.STATE_DIR = tmp
base.ENV_FILE = tmp / "no.env"
alpaca_mod.time.sleep = lambda s: None
os.environ.update({k: "test" for k in [
    "ALPACA_API_KEY", "ALPACA_SECRET_KEY", "OANDA_TOKEN", "OANDA_ACCOUNT_ID",
    "BINANCE_TESTNET_KEY", "BINANCE_TESTNET_SECRET"]})


class Resp:
    def __init__(self, data, status=200):
        self.status_code, self._data = status, data
        self.content = json.dumps(data).encode()
        self.text = self.content.decode()

    def json(self):
        return self._data


class FakeSession:
    """Routes (METHOD, url regex) -> reply (a value, or a function of the call). Records calls."""
    def __init__(self, routes):
        self.routes, self.calls, self.headers = routes, [], {}

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for (m, pattern), reply in self.routes.items():
            if m == method and re.search(pattern, url):
                return Resp(reply(url, kw) if callable(reply) else reply)
        raise AssertionError(f"unexpected call {method} {url}")

    def get(self, url, **kw):
        return self.request("GET", url, **kw)


def calls(s, method, pattern):
    return [c for c in s.calls if c[0] == method and re.search(pattern, c[1])]


# --- Alpaca ---------------------------------------------------------------
held = {}
order_status = {"status": "filled"}
s = FakeSession({
    ("GET", r"/v2/account$"): {"cash": "90000", "equity": "100500", "last_equity": "100000"},
    ("GET", r"/v2/positions$"): lambda u, k: [
        {"symbol": sym, "qty": str(q), "side": "long" if q > 0 else "short", "avg_entry_price": "200"}
        for sym, q in held.items()],
    ("POST", r"/v2/orders$"): lambda u, k: {"id": "o1", "filled_qty": "10", "filled_avg_price": "201.5", **order_status},
    ("GET", r"/v2/orders/o1$"): lambda u, k: {"id": "o1", "filled_qty": "10", "filled_avg_price": "201.5", **order_status},
    ("GET", r"/v2/orders$"): [{"id": "stop1"}],
    ("DELETE", r"/v2/orders/stop1$"): {},
    ("DELETE", r"/v2/positions/AAPL$"): {"filled_avg_price": "210"},
})
b = AlpacaPaper("us_stocks", s)
assert b.cash == 90000 and b.equity({}) == 100500 and b.day_start_equity({}) == 100000
t = b.open("AAPL", 1, 10, 200, 190.123, "SMA 50/200 crossover", 70)
body = calls(s, "POST", r"/v2/orders$")[0][2]["json"]
assert body["order_class"] == "oto" and body["stop_loss"] == {"stop_price": "190.12"} and body["qty"] == "10"
assert t["action"] == "OPEN" and t["price"] == 201.5
b.open("NVDA", 1, 5, 200, 190, "Supertrend", 70, target=220)
body = calls(s, "POST", r"/v2/orders$")[-1][2]["json"]
assert body["order_class"] == "bracket" and body["take_profit"] == {"limit_price": "220.00"}, body
held["AAPL"] = 10  # Alpaca holds the AAPL bought above (checked again further down)
s.routes[("GET", r"/v2/orders$")] = [{"id": "p1", "type": "market", "status": "filled",
                                     "legs": [{"id": "stopleg", "type": "stop", "status": "held"}]}]
s.routes[("PATCH", r"/v2/orders/stopleg$")] = {}
held["NVDA"] = 5
b.refresh()
b.update_stop("NVDA", 205.0, 230.0, 10.0)
assert calls(s, "PATCH", r"stopleg")[-1][2]["json"] == {"stop_price": "205.00"}
assert b.journal["positions"]["NVDA"]["stop"] == 205.0
s.routes[("DELETE", r"/v2/orders/p1$")] = {}
s.routes[("DELETE", r"/v2/orders/stopleg$")] = {}
s.routes[("DELETE", r"/v2/positions/NVDA$")] = {"filled_avg_price": "225"}
b.close("NVDA", 225, "test", qty=2)
d = calls(s, "DELETE", r"/v2/positions/NVDA")[-1][2]
assert d.get("params") == {"qty": "2"}, d
oco = calls(s, "POST", r"/v2/orders$")[-1][2]["json"]
assert oco["order_class"] == "oco" and oco["qty"] == "3" and oco["stop_loss"] == {"stop_price": "205.00"}, oco
assert b.journal["positions"]["NVDA"]["qty"] == 3
held["NVDA"] = 3  # what Alpaca now holds
b.refresh()
b.set_exits("NVDA", 210.0, None)
plain = calls(s, "POST", r"/v2/orders$")[-1][2]["json"]
assert plain["type"] == "stop" and plain["stop_price"] == "210.00" and plain["qty"] == "3", plain
held.pop("NVDA")
s.routes[("GET", r"/v2/orders$")] = [{"id": "stop1"}]
b.journal["positions"].pop("NVDA")
held["AAPL"] = 10
b.refresh()
assert b.positions["AAPL"]["strategy"] == "SMA 50/200 crossover" and b.positions["AAPL"]["managed"]
b.close("AAPL", 209, "test")
assert calls(s, "DELETE", r"stop1") and calls(s, "DELETE", r"/v2/positions/AAPL"), "stop must be cancelled before closing"
held.clear()
b.refresh()
assert b.positions == {}
# Market closed: order stays pending, is skipped next run, then dropped once cancelled.
order_status["status"] = "accepted"
t = b.open("MSFT", 1, 5, 500, 480, "EMA 12/26 crossover", 60)
assert t["action"].startswith("ORDER SENT") and "MSFT" in b.pending and "MSFT" not in b.positions
order_status["status"] = "canceled"
b.refresh()
assert b.pending == set() and "MSFT" not in b.journal["positions"]
# A position you opened yourself is visible but not managed.
held["TSLA"] = 3
b.refresh()
assert b.positions["TSLA"]["managed"] is False
print("alpaca  ok")

# --- OANDA ----------------------------------------------------------------
fx_pos = []
s = FakeSession({
    ("GET", r"/summary$"): {"account": {"balance": "100000", "NAV": "100120.5", "currency": "USD"}},
    ("GET", r"/openPositions$"): lambda u, k: {"positions": fx_pos},
    ("POST", r"/orders$"): lambda u, k: {"orderFillTransaction": {"units": k["json"]["order"]["units"], "price": "157.912"}},
    ("PUT", r"/positions/USD_JPY/close$"): {"shortOrderFillTransaction": {"price": "157.1"}},
})
b = OandaPractice("forex", s)
assert b.margin and b.equity({}) == 100120.5
b.open("USDJPY=X", -1, 20000, 157.9, 160.5123, "EMA 12/26 crossover", 41, fx=1 / 157.9)
order = calls(s, "POST", r"/orders$")[0][2]["json"]["order"]
assert order["units"] == "-20000" and order["instrument"] == "USD_JPY" and order["stopLossOnFill"]["price"] == "160.512"
fx_pos.append({"instrument": "USD_JPY", "long": {"units": "0"}, "short": {"units": "-20000", "averagePrice": "157.912"}})
b.refresh()
assert b.positions["USDJPY=X"]["qty"] == -20000 and b.positions["USDJPY=X"]["managed"]
b.close("USDJPY=X", 157.1, "test")
assert calls(s, "PUT", r"/close$")[0][2]["json"] == {"shortUnits": "ALL"}
assert OandaPractice._price("EURUSD=X", 1.1234567) == "1.12346"
fx_pos.clear()
b.open("EURUSD=X", 1, 1000, 1.10, 1.09, "x", 50, target=1.12)
assert calls(s, "POST", r"/orders$")[-1][2]["json"]["order"]["takeProfitOnFill"] == {"price": "1.12000"}
fx_pos.append({"instrument": "EUR_USD", "long": {"units": "1000", "averagePrice": "1.10"}, "short": {"units": "0"}})
s.routes[("GET", r"/openTrades$")] = {"trades": [{"id": "77", "instrument": "EUR_USD"}]}
s.routes[("PUT", r"/trades/77/orders$")] = {}
b.refresh()
b.update_stop("EURUSD=X", 1.1, 1.12, 0.01)
assert calls(s, "PUT", r"/trades/77/orders")[-1][2]["json"] == {"stopLoss": {"price": "1.10000"}}
s.routes[("PUT", r"/positions/EUR_USD/close$")] = {"longOrderFillTransaction": {"price": "1.105"}}
b.close("EURUSD=X", 1.105, "test", qty=400)
assert calls(s, "PUT", r"/positions/EUR_USD/close")[-1][2]["json"] == {"longUnits": "400"}
b.set_exits("EURUSD=X", 1.101, 1.13)
assert calls(s, "PUT", r"/trades/77/orders")[-1][2]["json"] == {"stopLoss": {"price": "1.10100"}, "takeProfit": {"price": "1.13000"}}
fx_pos.clear()
b.journal["positions"].pop("EURUSD=X")
s.routes[("POST", r"/orders$")] = {"orderCancelTransaction": {"reason": "MARKET_HALTED"}}
try:
    b.open("EURUSD=X", 1, 1000, 1.1, 1.09, "x", 50)
    raise AssertionError("expected BrokerError")
except BrokerError as e:
    assert "MARKET_HALTED" in str(e)
print("oanda   ok")

# --- Binance --------------------------------------------------------------
# Signature check against the worked example in Binance's API docs.
secret = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
query = "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1&recvWindow=5000&timestamp=1499827319559"
assert hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest() == \
    "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"

balances = {"USDT": 10000.0, "BTC": 1.0}  # testnet pre-loads coins like this BTC
s = FakeSession({
    ("GET", r"/api/v3/exchangeInfo"): {"symbols": [{"filters": [
        {"filterType": "LOT_SIZE", "stepSize": "0.00001000", "minQty": "0.00001000"},
        {"filterType": "NOTIONAL", "minNotional": "5.00000000"}]}]},
    ("GET", r"/api/v3/account\?"): lambda u, k: {"balances": [
        {"asset": a, "free": str(v), "locked": "0"} for a, v in balances.items()]},
    ("POST", r"/api/v3/order\?"): {"executedQty": "0.02345", "cummulativeQuoteQty": "1907.6"},
})
b = BinanceTestnet("crypto", s)
assert b.positions == {}, "pre-loaded testnet coins must not count as agent positions"
assert b.round_qty("BTC-USD", 0.0234567) == 0.02345
b.open("BTC-USD", 1, 0.0234567, 81350, 78000, "Donchian 20-day breakout", 66)
url = calls(s, "POST", r"/api/v3/order")[0][1]
assert "symbol=BTCUSDT" in url and "quantity=0.02345" in url and "signature=" in url
balances["BTC"] += 0.02345
b.refresh()
assert abs(b.positions["BTC-USD"]["qty"] - 0.02345) < 1e-12
assert abs(b.equity({"BTC-USD": 82000}) - (10000 + 0.02345 * 82000)) < 1e-6
try:
    b.open("ETH-USD", -1, 1, 2000, 2100, "x", 50)
    raise AssertionError("expected BrokerError")
except BrokerError as e:
    assert "short" in str(e)
print("binance ok")

# --- Missing keys give a clear message --------------------------------------
del os.environ["ALPACA_API_KEY"]
try:
    AlpacaPaper("us_stocks", FakeSession({}))
    raise AssertionError("expected BrokerError")
except BrokerError as e:
    assert "ALPACA_API_KEY" in str(e)
print("keys    ok")

shutil.rmtree(tmp)
print("all broker tests passed")
