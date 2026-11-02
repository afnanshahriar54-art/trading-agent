"""Take-profit: settings, target prices, the live watcher, and closing at the target or safety net.

Run: python tests/takeprofit_test.py
Uses throwaway settings and paper accounts; never touches your real ones.
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
from trader.risk import RiskConfig, RiskManager

tmp = Path(__file__).parent / ".tptest"
shutil.rmtree(tmp, ignore_errors=True)
tmp.mkdir()
br.STATE_DIR = srv.STATE_DIR = perf.STATE_DIR = tmp
perf.SNAPSHOTS = tmp / "account_history.json"
st.FILE = tmp / "settings.json"
daily.schedule = lambda *a: None
notes = []
daily.notify = lambda title, body: notes.append(body)
real_load = ag.load_config


def paper_config():
    cfg = real_load()
    for m in cfg["markets"].values():
        m["broker"] = "paper"
    return cfg


ag.load_config = paper_config
app = srv.App()
base = {"mode": "manual", "check": {"enabled": False, "time": "08:00"}, "auto": {"markets": ["crypto"], "methods": ["Breakout"]}}

print("1) Target math")
for mode, value, expect in [("risk", 2, 120), ("percent", 5, 105), ("amount", 50, 105), ("off", 0, None)]:
    t = RiskManager(RiskConfig(take_profit={"mode": mode, "value": value})).target_price(100, 90, 1, 10, 1.0)
    assert (t is None and expect is None) or abs(t - expect) < 1e-9, (mode, t)
    print(f"   buy at 100, safety net 90, 10 units, {mode} {value}: take-profit at {t}")
t = RiskManager(RiskConfig(take_profit={"mode": "risk", "value": 2})).target_price(100, 110, -1, 10)
assert t == 80
print(f"   short at 100, safety net 110, 2x risk: take-profit at {t}")

print("2) Settings validation")
for bad in [{"mode": "percent", "value": 500}, {"mode": "risk", "value": 0.1}, {"mode": "sometimes", "value": 1}]:
    try:
        app.save_settings({**base, "take_profit": bad})
        raise AssertionError(bad)
    except BrokerError as e:
        print(f"   refused {bad}: {e}")
app.save_settings({**base, "take_profit": {"mode": "risk", "value": 2}})
assert ag.load_config()["risk"]["take_profit"] == {"mode": "risk", "value": 2.0}

print("3) Your trade gets a take-profit; the watcher closes it when the live price reaches it")
q = app.manual_quote("crypto", "ETH-USD", 1, 3000)
assert q["target"] and abs((q["target"] - q["price"]) - 2 * (q["price"] - q["stop"])) < 1e-6
print(f"   preview: buy at {q['price']:.2f}, safety net {q['stop']:.2f}, take-profit {q['target']:.2f} (+{q['gain']:.0f})")
print("  ", app.manual_trade("crypto", "ETH-USD", 1, 3000))
pos = br.PaperBroker("crypto", 100000, 10, 10).positions["ETH-USD"]
assert pos["target"] == q["target"]
assert app.watch_once() == [], "nothing to do at today's price"
real_live = app.live_price
app.live_price = lambda m, s: (q["target"] * 1.001, "now", True)
closed = app.watch_once()
assert closed and "take-profit" in closed[0], closed
assert "ETH-USD" not in br.PaperBroker("crypto", 100000, 10, 10).positions
print(f"   watcher: {closed[0]}  | notification: {notes[-1]}")

print("4) ...and at the safety net")
app.live_price = real_live
q = app.manual_quote("crypto", "SOL-USD", 1, 3000)
app.manual_trade("crypto", "SOL-USD", 1, 3000)
app.live_price = lambda m, s: (q["stop"] * 0.999, "now", True)
closed = app.watch_once()
assert closed and "stop-loss" in closed[0], closed
print(f"   watcher: {closed[0]}")

print("5) Agent exits use the target too; Off means no target")
a = next(x for x in ag.scan(ag.load_config(), ["crypto"], verbose=False)[0]["crypto"] if x.symbol == "BTC-USD")
fake = {"qty": 1, "stop": a.price * 0.5, "target": a.price * 0.99, "strategy": ag.MANUAL}
assert "take-profit" in ag.exit_reason(fake, a)
app.save_settings({**base, "take_profit": {"mode": "off", "value": 2}})
app.live_price = real_live
assert app.manual_quote("crypto", "ETH-USD", 1, 3000)["target"] is None
print("   ok")

print("6) The watcher leaves Alpaca/OANDA trades to the broker")
cfg = paper_config()
cfg["markets"]["us_stocks"]["broker"] = "alpaca"
ag.load_config = lambda: cfg
app.watch_once()  # must not try to connect to Alpaca
print("   ok")

print("7) Profit protection: trailing and break-even move the safety net, never back")
from trader.protect import new_stop
pos = {"qty": 10, "entry": 100.0, "stop": 90.0, "stop0": 90.0}
trail = {"trailing": {"on": True, "distance": 1.5}, "breakeven": {"on": False, "at": 1}}
assert new_stop(pos, 100, trail)[0] == 90.0                 # not up yet: stays at 90 (100 - 15 = 85 < 90)
stop, best, r = new_stop(pos, 120, trail)
assert (stop, best, r) == (105.0, 120, 10.0)                 # best 120 - 1.5 x 10 = 105
pos.update(stop=stop, best=best, risk_dist=r)
assert new_stop(pos, 110, trail) is None                     # price falls back: safety net stays at 105
be = {"breakeven": {"on": True, "at": 1.0}, "trailing": {"on": False, "distance": 1.5}}
assert new_stop({"qty": 10, "entry": 100.0, "stop": 90.0}, 110, be)[0] == 100.0   # up 1R: to the buy price
assert new_stop({"qty": -10, "entry": 100.0, "stop": 110.0}, 80, trail)[0] == 95.0  # short: 80 + 15
print("   moves correctly for buys and shorts")
try:
    app.save_settings({**base, "protect": {"trailing": {"on": True, "distance": 99}}})
    raise AssertionError("range")
except BrokerError as e:
    print(f"   refused: {e}")
app.save_settings({**base, "take_profit": {"mode": "off", "value": 2},
                   "protect": {"trailing": {"on": True, "distance": 1.5}, "breakeven": {"on": False, "at": 1}}})
ag.load_config = paper_config
app.live_price = real_live
q = app.manual_quote("crypto", "ETH-USD", 1, 3000)
app.manual_trade("crypto", "ETH-USD", 1, 3000)
r = q["price"] - q["stop"]
app.live_price = lambda m, s: (q["price"] + 3 * r, "now", True)     # rallies 3R: safety net to +1.5R
assert app.watch_once() == []
moved = br.PaperBroker("crypto", 100000, 10, 10).positions["ETH-USD"]
assert moved["stop"] > moved["entry"], moved
print(f"   watcher moved the safety net from {moved['stop0']:.2f} to {moved['stop']:.2f} (above the buy price {moved['entry']:.2f})")
app.live_price = lambda m, s: (moved["stop"] * 0.999, "now", True)  # falls back: closes with a profit locked in
closed = app.watch_once()
assert closed and "+" in closed[0], closed
print(f"   then it fell back and the watcher closed it: {closed[0]}")

shutil.rmtree(tmp)
print("take-profit test passed")
