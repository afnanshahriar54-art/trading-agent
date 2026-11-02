"""Manual/Auto modes, settings validation and trades placed from the chart panel.

Run: python tests/modes_test.py
Uses throwaway settings and paper accounts; never touches your real ones or Windows tasks.
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

tmp = Path(__file__).parent / ".modestest"
shutil.rmtree(tmp, ignore_errors=True)
tmp.mkdir()
br.STATE_DIR = srv.STATE_DIR = perf.STATE_DIR = tmp
perf.SNAPSHOTS = tmp / "account_history.json"
st.FILE = tmp / "settings.json"
daily.LAST_RUN = tmp / "daily_check.json"
ag.REPORT_DIR = tmp
daily.schedule = lambda enabled, at: print(f"   (would {'schedule' if enabled else 'remove'} the Windows task at {at})")
daily.notify = lambda title, body: print(f"   (notification: {body})")
real_load = ag.load_config


def paper_config():
    cfg = real_load()
    for m in cfg["markets"].values():
        m["broker"] = "paper"
    return cfg


ag.load_config = paper_config
app = srv.App()
real_limits = dict(st.RULE_LIMITS)

print("1) Settings validation")
markets = list(paper_config()["markets"])
for bad, why in [({"mode": "auto", "check": {"enabled": False}, "auto": {"markets": ["crypto"], "methods": ["Breakout"]}}, "needs check"),
                 ({"mode": "auto", "check": {"enabled": True}, "auto": {"markets": [], "methods": ["Breakout"]}}, "needs market"),
                 ({"mode": "manual", "rules": {"risk_per_trade_pct": 9}}, "rule range")]:
    try:
        app.save_settings(bad)
        raise AssertionError(f"should refuse ({why})")
    except BrokerError as e:
        print(f"   refused ({why}): {e}")

print("2) Manual mode + rules apply everywhere")
st.RULE_LIMITS["min_score"] = (0, 95)  # this test needs low scores so that today's ideas qualify
app.save_settings({"mode": "manual", "check": {"enabled": True, "time": "08:00"},
                   "auto": {"markets": ["crypto"], "methods": ["Breakout"]}, "rules": {"min_score": 20, "risk_per_trade_pct": 0.5}})
cfg = ag.load_config()
assert cfg["risk"]["min_score"] == 20 and cfg["risk"]["risk_per_trade_pct"] == 0.5, "saved rules must override config.yaml"
r = daily.check()
assert not r["opened"], "manual mode must never trade"
print(f"   manual daily check: {r['summary']}")

print("3) Auto mode trades only your markets and methods")
app.save_settings({"mode": "auto", "check": {"enabled": True, "time": "08:00"},
                   "auto": {"markets": ["forex"], "methods": ["Short-term trend"]}, "rules": {"min_score": 20}})
r = daily.check()
print(f"   auto daily check: {r['summary']}")
forex = br.PaperBroker("forex", 100000, 1, 1, True)
assert all(p["strategy"] == "Short-term trend" for p in forex.positions.values()), "only allowed methods"
assert not (tmp / "us_stocks.json").exists() or not br.PaperBroker("us_stocks", 100000, 1, 3).positions, "only allowed markets"
print(f"   forex positions opened by auto mode: {list(forex.positions)}")

print("4) Your own trade from the chart panel")
q = app.manual_quote("crypto", "ETH-USD", 1, 2000)
print(f"   preview: buy {q['qty']:g} ETH, size {q['size']:,.0f}, safety net {q['stop']:.2f}, max loss {q['risk']:,.0f}, live={q['live']}")
print("  ", app.manual_trade("crypto", "ETH-USD", 1, 2000))
pos = br.PaperBroker("crypto", 100000, 10, 10).positions["ETH-USD"]
assert pos["strategy"] == ag.MANUAL
too_big = app.manual_quote("crypto", "BTC-USD", 1, 95000)
assert not too_big["ok"]
print(f"   too big refused: {too_big['why']}")
try:
    app.manual_quote("crypto", "BTC-USD", -1)
    raise AssertionError("short selling crypto should be refused")
except BrokerError as e:
    print(f"   short refused: {e}")
# The agent must not close your trade just because no method signals it.
a = next(x for x in ag.scan(ag.load_config(), ["crypto"], verbose=False)[0]["crypto"] if x.symbol == "ETH-USD")
assert ag.exit_reason(pos, a) is None, "only the safety net closes your own trades"
print("  ", app.close("crypto", "ETH-USD"))
who = app.results()["practice"]["by_who"]
assert who["Your own trades"]["trades"] == 1
print(f"   results split: {who}")

print("5) Live chart prices")
for m, s in [("us_stocks", "AAPL"), ("crypto", "BTC-USD")]:
    c = app.chart(m, s, "day")
    print(f"   {s}: {len(c['bars'])} bars, last {c['last']:.2f} at {c['last_time']}, live={c['live']}, from {c['source']}")

shutil.rmtree(tmp)
print("modes test passed")
