"""Closing options and live profit/loss: partial close, close all/winners/losers, changing exits.

Run: python tests/closing_test.py
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

tmp = Path(__file__).parent / ".closetest"
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
crypto = lambda: br.PaperBroker("crypto", 100000, 10, 10)

print("1) Live prices and live profit/loss")
for m, s in [("crypto", "BTC-USD"), ("us_stocks", "AAPL"), ("forex", "EURUSD=X")]:
    q = app.quote(m, s)
    print(f"   {s:9s} {q['price']:>12,.4f} live={q['live']}")
app.manual_trade("crypto", "ETH-USD", 1, 4000)
app.manual_trade("crypto", "SOL-USD", 1, 3000)
app.manual_trade("forex", "EURUSD=X", 1, 15000)
live = app.live_trades()
assert len(live["trades"]) == 3, live
for t in live["trades"]:
    print(f"   {t['symbol']:9s} now {t['price']:,.4f}  P&L {t['pnl']:+,.2f} ({t['pnl_pct']:+.2f}%)")
print(f"   total {live['total_usd']:+,.2f} at {live['time']}")

print("2) Close part of a trade")
before = crypto().positions["ETH-USD"]["qty"]
print("  ", app.close("crypto", "ETH-USD", 50))
after = crypto().positions["ETH-USD"]["qty"]
assert abs(after - before / 2) < 1e-6, (before, after)
print(f"   ETH: {before:.6f} -> {after:.6f}, rest still open with its safety net {crypto().positions['ETH-USD']['stop']:.2f}")
try:
    app.close("crypto", "ETH-USD", 150)
    raise AssertionError("over 100% must be refused")
except BrokerError as e:
    print(f"   refused: {e}")

print("3) Change the safety net and take-profit by hand")
pos = crypto().positions["SOL-USD"]
price = app.live_price("crypto", "SOL-USD")[0]
for stop, target, why in [(pos["stop0"] * 0.9, None, "looser than at the start"),
                          (price * 1.01, None, "above today's price"),
                          (pos["stop"], price * 0.99, "take-profit below today's price")]:
    try:
        app.set_exits("crypto", "SOL-USD", stop, target)
        raise AssertionError(why)
    except BrokerError as e:
        print(f"   refused ({why}): {e}")
tighter = (pos["stop"] + price) / 2
print("  ", app.set_exits("crypto", "SOL-USD", tighter, price * 1.05))
new = crypto().positions["SOL-USD"]
assert abs(new["stop"] - tighter) < 1e-9 and abs(new["target"] - price * 1.05) < 1e-9
print("  ", app.set_exits("crypto", "SOL-USD", tighter, None))
assert crypto().positions["SOL-USD"]["target"] is None

print("4) Close all / winners / losers")
live = {t["symbol"]: t["pnl"] for t in app.live_trades()["trades"]}
print("   P&L now:", {k: round(v, 2) for k, v in live.items()})
print("  ", app.close_many("winners"))
app.live_cache = (0.0, [])
left = {t["symbol"]: t["pnl"] for t in app.live_trades()["trades"]}
assert all(v <= 0 for v in left.values()), left
print("  ", app.close_many("all"))
app.live_cache = (0.0, [])
assert app.live_trades()["trades"] == []
print("   nothing left open")
closes = [t for t in crypto().state["trades"] if t["action"] == "CLOSE"]
print(f"   history recorded {len(closes)} crypto closes, each with its profit/loss")

shutil.rmtree(tmp)
print("closing test passed")
