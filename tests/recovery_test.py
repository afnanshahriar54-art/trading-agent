"""Order recovery: what happens when the broker's answer gets lost or the app stops mid-order.

Run: python tests/recovery_test.py
Uses a fake broker in a throwaway folder; no real account is contacted.
"""
import shutil
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import trader.live.base as base
from trader.live import BrokerError

tmp = Path(__file__).parent / ".recoverytest"
shutil.rmtree(tmp, ignore_errors=True)
tmp.mkdir()
base.STATE_DIR = tmp


class FakeBroker(base.LiveBroker):
    """A broker whose behaviour each test step sets: what the order call does, what it holds."""
    name = "fake"

    def __init__(self):
        super().__init__("crypto")
        self.held, self.orders, self.on_submit, self.sent = {}, {}, "fill", 0

    def _account(self):
        return {"cash": 1000.0, "equity": 1000.0, "day_start": 1000.0}

    def _broker_positions(self):
        return dict(self.held)

    def _submit(self, symbol, side, qty, stop, target=None, cid=None):
        self.sent += 1
        if self.on_submit == "reject":
            raise BrokerError("insufficient balance")
        self.orders[cid] = "filled" if self.on_submit != "lost" else "gone"
        if self.on_submit in ("fill", "timeout_after_fill"):
            self.held[symbol] = (side * qty, 100.0)
        if self.on_submit.startswith("timeout") or self.on_submit == "lost":
            raise requests.Timeout("read timed out")
        return qty, 100.0, None

    def _order_status(self, cid):
        return self.orders.get(cid)

    def _close(self, symbol, qty, part=None):
        self.held.pop(symbol, None)
        return 100.0


# 1. A clear "no" from the broker: nothing is written down, you can try again.
b = FakeBroker()
b.on_submit = "reject"
try:
    b.open("BTC-USD", 1, 1, 100, 90, "test", 50)
    raise AssertionError("should fail")
except BrokerError:
    pass
assert "BTC-USD" not in b.journal["positions"]
print("   rejected order: nothing recorded, free to try again")

# 2. The order went through but the answer was lost (timeout).
b = FakeBroker()
b.on_submit = "timeout_after_fill"
try:
    b.open("ETH-USD", 1, 2, 100, 90, "test", 50)
    raise AssertionError("should report no clear answer")
except BrokerError as e:
    assert "No clear answer" in str(e)
assert b.journal["positions"]["ETH-USD"]["uncertain"]
try:  # a retry must not send a second order
    b.open("ETH-USD", 1, 2, 100, 90, "test", 50)
    raise AssertionError("duplicate allowed")
except BrokerError:
    pass
assert b.sent == 1, b.sent
pos = b.positions["ETH-USD"]
assert pos["managed"] and pos["stop"] == 90 and pos["qty"] == 2, pos
print("   lost answer after a fill: no second order, and the trade is tracked with its safety net")

# 3. The app stopped mid-order and the broker never got it: cleaned up after asking.
b = FakeBroker()
b.on_submit = "lost"
try:
    b.open("SOL-USD", 1, 3, 100, 90, "test", 50)
except BrokerError:
    pass
assert b.journal["positions"]["SOL-USD"]["uncertain"]  # written down before sending
b2 = FakeBroker()              # "restart": a fresh app reading the saved journal
b2.orders = b.orders
assert "SOL-USD" not in b2.positions and "SOL-USD" not in b2.journal["positions"]
assert b2.journal["trades"][-1]["action"] == "ORDER NOT PLACED"
b2.on_submit = "fill"
b2.open("SOL-USD", 1, 3, 100, 90, "test", 50)
assert b2.positions["SOL-USD"]["qty"] == 3
print("   order that never arrived: found out after restart, recorded, and a new order works")

# 4. The broker can't say (no lookup): the order keeps blocking for 15 minutes, then is released.
b = FakeBroker()
b.on_submit = "lost"
b.orders = {}
try:
    b.open("XRP-USD", 1, 1, 1, 0.9, "test", 50)
except BrokerError:
    pass
b._order_status = lambda cid: None
b.refresh()
assert "XRP-USD" in b.pending
b.journal["positions"]["XRP-USD"]["opened"] = "2000-01-01T00:00:00"
b.refresh()
assert "XRP-USD" not in b.pending
print("   unknown outcome: blocks a repeat for 15 minutes, then frees the asset")

shutil.rmtree(tmp, ignore_errors=True)
print("recovery test passed")
