"""Any amount, any trade: parts of a US share, single forex units, small crypto, warnings past your limits.

Run: python tests/amounts_test.py
Uses throwaway settings and paper accounts; never touches your real ones. The Alpaca part uses
a fake connection, so no order reaches your Alpaca account.
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import trader.agent as ag
import trader.broker as br
import trader.daily as daily
import trader.dashboard.server as srv
import trader.performance as perf
import trader.settings as st
from trader.live import BrokerError
from trader.live import alpaca as alp

tmp = Path(__file__).parent / ".amounttest"
shutil.rmtree(tmp, ignore_errors=True)
tmp.mkdir()
br.STATE_DIR = srv.STATE_DIR = perf.STATE_DIR = tmp
perf.SNAPSHOTS = tmp / "account_history.json"
st.FILE = tmp / "settings.json"
daily.schedule = lambda *a: None
daily.notify = lambda *a: None
real_load = ag.load_config


def paper_config():
    cfg = real_load()
    for m in cfg["markets"].values():
        m["broker"] = "paper"
    return cfg


ag.load_config = paper_config
app = srv.App()

# 1. Small amounts in every market
for market, symbol, amount in [("us_stocks", "AAPL", 1), ("crypto", "BTC-USD", 1), ("forex", "EURUSD=X", 5)]:
    q = app.manual_quote(market, symbol, 1, amount)
    assert q["ok"], (symbol, q["why"])
    assert q["qty"] > 0 and q["size"] <= amount * 1.0001, (symbol, q["qty"], q["size"])
    print(f"   ${amount} of {symbol}: {q['qty']:g} {q['units']} (worth {q['size']:.2f})")
q = app.manual_quote("us_stocks", "AAPL", 1, 0.5)
assert not q["ok"] and "$1" in q["why"], q["why"]
ag.load_config = lambda: {**(c := paper_config()), "markets": {**c["markets"], "us_stocks": {**c["markets"]["us_stocks"], "allow_short": True}}}
q = app.manual_quote("us_stocks", "AAPL", -1, 1)
assert not q["ok"] and "whole share" in q["why"], q["why"]
print("   under $1 at Alpaca and short selling part of a share are refused, with the reason")

# 2. Past the safety limits: a warning, and it needs your OK
q = app.manual_quote("crypto", "BTC-USD", 1, 60000)
assert q["ok"] and q["warnings"], q
try:
    app.manual_trade("crypto", "BTC-USD", 1, 60000)
    raise AssertionError("should ask to confirm the warnings")
except BrokerError as e:
    assert "confirm" in str(e)
msg = app.manual_trade("crypto", "BTC-USD", 1, 60000, accept=True)
assert msg.startswith("Done"), msg
print("   a trade past your limits shows", len(q["warnings"]), "warning(s) and goes through once you confirm")

# 3. Small trades really open, and partial close works on parts of a share
msg = app.manual_trade("us_stocks", "AAPL", 1, 3)
assert msg.startswith(("Done", "Order sent")), msg
b = ag.make_broker("us_stocks", app.cfg)
held = b.positions["AAPL"]["qty"]
assert 0 < held < 1, held
app.close("us_stocks", "AAPL", 50)
left = ag.make_broker("us_stocks", app.cfg).positions["AAPL"]["qty"]
assert abs(left - held / 2) < 1e-5, (held, left)
print(f"   bought {held:g} of an Apple share for $3, then closed half: {left:g} left")


# 4. Alpaca: a fractional buy is a plain market order, and the app keeps its safety net
class FakeAlpaca(alp.AlpacaPaper):
    def __init__(self):  # no keys, no network
        self.market, self.name, self.sent = "us_stocks", "alpaca", []
        self.journal = {"positions": {}, "trades": []}
        self.journal_path = tmp / "fake_alpaca.json"
        self._cache = {"account": {"cash": 1000, "equity": 1000, "day_start": 1000}, "positions": {}}

    def _req(self, method, path, **kw):
        self.sent.append((method, path, kw.get("json") or kw.get("params")))
        return {"id": "1", "status": "filled", "filled_qty": "0.0036", "filled_avg_price": "275"}

    def refresh(self):
        pass


fa = FakeAlpaca()
fa.open("AAPL", 1, 0.0036, 275, 260, "Your trade", 0)
body = fa.sent[-1][2]
assert body["qty"] == "0.0036" and body["time_in_force"] == "day" and "order_class" not in body, body
assert fa.journal["positions"]["AAPL"]["app_exits"] and not fa.exits_at_broker("AAPL")
fa.sent.clear()
fa.open("MSFT", 1, 2, 500, 480, "Your trade", 0)
assert fa.sent[-1][2]["order_class"] == "oto" and fa.exits_at_broker("MSFT")
try:
    fa.open("TSLA", -1, 0.5, 400, 420, "Your trade", 0)
    raise AssertionError("short fractional must be refused")
except BrokerError:
    pass
print("   Alpaca: part of a share = simple order watched by the app; whole shares keep their exits at Alpaca")

shutil.rmtree(tmp, ignore_errors=True)
print("amounts test passed")
