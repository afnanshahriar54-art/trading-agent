"""Methods page: switching methods on/off, the method builder, and the per-method report card.

Run: python tests/methods_test.py
Uses throwaway settings; never touches your real ones.
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import trader.agent as ag
import trader.dashboard.server as srv
import trader.replay as replay
import trader.settings as st
from trader import methods
from trader.live import BrokerError

tmp = Path(__file__).parent / ".methodstest"
shutil.rmtree(tmp, ignore_errors=True)
tmp.mkdir()
st.FILE = tmp / "settings.json"
replay.METHOD_FILE = tmp / "method_card.json"
app = srv.App()

print("1) Defaults: original 7 on, the 13 newer ones off")
v = app.methods_view()
on = [m["name"] for m in v["methods"] if m["enabled"]]
assert len(on) == 7 and len(v["methods"]) == 20, (len(on), len(v["methods"]))
print(f"   {len(v['methods'])} methods, on: {', '.join(on)}")

print("2) Switching off removes a method from trade ideas")
app.change_methods({"action": "toggle", "name": "Breakout", "on": False})
app.change_methods({"action": "toggle", "name": "Supertrend", "on": True})
names_on = methods.active_names()
assert "Breakout" not in names_on and "Supertrend" in names_on
_, cands = ag.scan(ag.load_config(), ["crypto", "forex"], verbose=False)
used = {c.view.strategy.name for c in cands}
assert "Breakout" not in used, used
print(f"   ideas came only from switched-on methods: {sorted(used) or 'none today'}")
try:
    for name in list(names_on):
        app.change_methods({"action": "toggle", "name": name, "on": False})
    raise AssertionError("must keep one method on")
except BrokerError as e:
    print(f"   refused: {e}")

print("3) Method builder")
mine = {"name": "Cheap in an uptrend", "family": "mean_reversion",
        "entry": [{"type": "price_vs_ma", "op": "above", "n": 200}, {"type": "rsi", "op": "below", "n": 14, "x": 35}],
        "exit": [{"type": "rsi", "op": "above", "n": 14, "x": 60}]}
t = app.test_method(mine)
print(f"   {t['rule']}")
for m, r in t["results"].items():
    print(f"   {m:10s} {r['avg_return']:+.0%} vs holding {r['hold_return']:+.0%}, {r['trades']} trades, {r['win_rate']:.0%} won")
for bad, why in [({**mine, "entry": []}, "no conditions"), ({**mine, "entry": [{"type": "rsi", "op": "below", "n": 14, "x": 150}]}, "bad value"),
                 ({**mine, "name": "Breakout"}, "name taken")]:
    try:
        app.change_methods({"action": "add", "method": bad})
        raise AssertionError(why)
    except BrokerError as e:
        print(f"   refused ({why}): {e}")
app.change_methods({"action": "add", "method": mine})
v = app.methods_view()
saved = next(m for m in v["methods"] if m["name"] == "Cheap in an uptrend")
assert saved["custom"] and not saved["enabled"], "your method is saved switched off"
app.change_methods({"action": "toggle", "name": "Cheap in an uptrend", "on": True})
a = ag.analyzer.analyze_symbol("us_stocks", "SPY", ag.load_config()["markets"]["us_stocks"], ag.load_config())
assert a.target_of("Cheap in an uptrend") is not None
print("   saved, switched on, and analysed by the agent")

print("4) Per-method report card includes your method")
card = replay.method_card(ag.load_config())
assert "Cheap in an uptrend" in card["methods"] and len(card["methods"]) == 21
print(f"   tested {len(card['methods'])} methods")
app.change_methods({"action": "delete", "name": "Cheap in an uptrend"})
assert all(m["name"] != "Cheap in an uptrend" for m in app.methods_view()["methods"])
print("   deleted")

shutil.rmtree(tmp)
print("methods test passed")
