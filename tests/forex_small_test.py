"""Small forex trades: $5 / $10 / $25 / $50 / custom amounts, unit rounding, margin, limits, failures.

Run: python tests/forex_small_test.py
Uses throwaway settings and paper accounts, fixed prices and a fake OANDA connection;
never touches your real accounts and never places a real order.
"""
import json
import math
import os
import shutil
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import trader.agent as ag
import trader.broker as br
import trader.daily as daily
import trader.dashboard.server as srv
import trader.live.base as lb
import trader.performance as perf
import trader.risk as risk_mod
import trader.settings as st
from trader import forex_size as fs
from trader import fx
from trader.live import BrokerError
from trader.live import oanda as oa
from trader.risk import RiskConfig, RiskManager, order_problem

tmp = Path(__file__).parent / ".forextest"
shutil.rmtree(tmp, ignore_errors=True)
tmp.mkdir()
br.STATE_DIR = srv.STATE_DIR = perf.STATE_DIR = lb.STATE_DIR = tmp
perf.SNAPSHOTS = tmp / "account_history.json"
st.FILE = tmp / "settings.json"
daily.schedule = lambda *a: None
daily.notify = lambda *a: None
real_load = ag.load_config
overrides = {}


def paper_config():
    cfg = real_load()
    for m in cfg["markets"].values():
        m["broker"] = "paper"
    cfg["markets"]["forex"].update(overrides)
    return cfg


ag.load_config = paper_config
PRICES = {"EURUSD=X": 1.0850, "GBPUSD=X": 1.2710, "USDJPY=X": 151.37, "AUDUSD=X": 0.6542, "USDCHF=X": 0.8812}
QUOTE = {s: (1 / p if fx.pair(s)[0] == "USD" else 1.0) for s, p in PRICES.items()}
spec = fs.Spec()

# 1. Dollars -> units for every supported pair: rounded down, never above the amount, as big as allowed
for sym, price in PRICES.items():
    for usd in fs.PRESETS_USD + (7.77, 1000):
        u = fs.units_for_usd(usd, price, QUOTE[sym], spec)
        one = fs.usd_per_unit(price, QUOTE[sym])
        value = u * one
        assert isinstance(u, Decimal) and u == u.to_integral_value(), (sym, u)
        assert value <= Decimal(str(usd)), (sym, usd, value)                 # never above the amount
        assert Decimal(str(usd)) - value < one, (sym, usd, value)            # and no whole unit left out
print("   $5/$10/$25/$50/custom -> units for EUR/USD, GBP/USD, USD/JPY, AUD/USD, USD/CHF: rounded down, never over")
assert fs.units_for_usd(10, 1.085, 1.0, spec) == 9           # 9 EUR = $9.765
cross = fs.units_for_usd(25, 0.8536, 1.2710, spec)           # EUR/GBP, GBP worth $1.271
assert cross * Decimal("0.8536") * Decimal("1.2710") <= 25
print("   cross pair (EUR/GBP) converts through the quote currency's dollar rate")

# 2. Broker unit rules: precision, minimum, maximum, never rounding up
p2 = fs.Spec(min_units=Decimal("0.01"), precision=2, max_units=Decimal(500))
assert fs.normalize("12.349", p2) == Decimal("12.34")
assert fs.normalize("0.009", p2) == 0
assert fs.normalize(10_000, p2) == 500
assert fs.normalize(5.99, fs.Spec(min_units=Decimal(10))) == 0
assert fs.normalize(-3, spec) == 0
print("   unit steps: 2 decimals -> 12.34 (not 12.35), under the minimum -> 0, capped at the maximum")

# 3. Margin is not the trade's size
pl = fs.plan(23, 1.085, 1.075, 1.0, spec, {"fee_bps": 1, "slippage_bps": 2, "carry_pct_year": 2})
assert abs(float(pl.notional) - 24.955) < 1e-9
assert abs(float(pl.margin) - 24.955 * 0.0333) < 1e-9 and pl.margin < pl.notional / 20
assert abs(float(pl.stop_loss) - 23 * 0.01) < 1e-9
assert abs(float(pl.costs) - 24.955 * 0.0003 * 2) < 1e-9
print(f"   a ${float(pl.notional):.2f} EUR/USD trade needs ${float(pl.margin):.2f} margin, costs ${float(pl.costs):.4f}")

# 4. Invalid prices are refused everywhere
for bad in (float("nan"), float("inf"), 0, -1.2):
    try:
        fs.usd_per_unit(bad, 1.0)
        raise AssertionError(f"accepted price {bad}")
    except ValueError:
        pass
    assert order_problem(1, 10, bad, 1.0) is not None
assert order_problem(1, 10, 1.085, 1.09) and order_problem(-1, 10, 1.085, 1.08)  # wrong-side safety nets
print("   NaN / infinite / zero / negative prices and wrong-side safety nets are refused")

# 5. The trade panel: presets, preview, too-small amounts, limits and margin
app = srv.App()
app.live_price = lambda market, symbol: (PRICES.get(symbol, 1.0), "now", True)
for sym in PRICES:
    for usd in fs.PRESETS_USD:
        q = app.manual_quote("forex", sym, 1, usd)
        x = q["forex"]
        assert q["ok"], (sym, usd, q["why"])
        assert x["notional"] <= usd + 1e-9 and q["qty"] == x["units"] and q["qty"] >= 1
        assert abs(x["margin"] - x["notional"] * x["margin_rate"]) < 1e-9 and x["margin"] < x["notional"]
        for k in ("stop_loss", "costs", "financing_day", "margin_free"):
            assert math.isfinite(x[k]) and x[k] >= 0, (k, x)
print("   trade panel: every preset works for every pair, with size, loss, costs and margin shown")
q = app.manual_quote("forex", "EURUSD=X", 1, 0.5)
assert not q["ok"] and "smallest" in q["why"], q["why"]
print(f"   $0.50 of EUR/USD is refused: {q['why']}")
q = app.manual_quote("forex", "EURUSD=X", 1, 2_500_000)
assert q["ok"] and q["warnings"], q
print(f"   a huge amount still shows your limits as warnings ({len(q['warnings'])})")
overrides["margin_rate"] = 1.0                      # a broker that wants the full size as deposit
q = app.manual_quote("forex", "EURUSD=X", 1, 150_000)
assert not q["ok"] and "free margin" in q["why"], q["why"]
overrides.clear()
print(f"   not enough margin is refused: {q['why'][:60]}...")
killed = not risk_mod.KILL_SWITCH.exists()
if killed:
    risk_mod.KILL_SWITCH.write_text("test")
try:
    q = app.manual_quote("forex", "EURUSD=X", 1, 10)
    assert not q["ok"] and "paused" in q["why"]
finally:
    if killed:
        risk_mod.KILL_SWITCH.unlink()
print("   the stop switch still blocks small trades")
app.live_price = lambda market, symbol: (float("nan"), "now", True)
try:
    app.manual_quote("forex", "EURUSD=X", 1, 10)
    raise AssertionError("broken price accepted")
except BrokerError:
    pass
app.live_price = lambda market, symbol: (PRICES.get(symbol, 1.0), "now", True)
print("   a broken live price stops the preview")

# 6. Placing a small trade on the practice account, then the duplicate guard
msg = app.manual_trade("forex", "USDJPY=X", 1, 10)
assert msg.startswith("Done"), msg
held = ag.make_broker("forex", ag.load_config()).positions["USDJPY=X"]["qty"]
assert held in (9, 10), held
q = app.manual_quote("forex", "USDJPY=X", 1, 10)
assert not q["ok"] and "already" in q["why"]
print(f"   a $10 USD/JPY trade opened {held:g} units; a second one in the same pair is blocked")

# 7. The agent (trade ideas / Auto mode): same engine, fixed-dollar mode, risk rule still caps it
cfg = ag.load_config()
b = ag.make_broker("forex", cfg)
rm = RiskManager(RiskConfig(**cfg["risk"]))


def cand(sym, score=80):
    a = SimpleNamespace(price=PRICES[sym], atr=PRICES[sym] * 0.006, fx=QUOTE[sym])
    return SimpleNamespace(analysis=a, market="forex", symbol=sym, side=1, score=score,
                           view=SimpleNamespace(strategy=SimpleNamespace(name="test")))


def prop(conf, score=80):
    return ag.propose(cand("GBPUSD=X", score), b, rm, {"GBPUSD=X": PRICES["GBPUSD=X"]}, {"GBPUSD=X": 1.0}, conf)


p = prop(cfg)
assert p.ok and p.qty == int(p.qty) and p.qty > 0, p
risk_sized = p.qty
p = prop({**cfg, "forex_size": {"mode": "notional", "usd": 25}})
assert p.ok and p.qty == 19 and p.notional <= 25, (p.qty, p.notional)   # 19 GBP = $24.15
p = prop({**cfg, "forex_size": {"mode": "notional", "usd": 10_000_000}})
assert p.qty == risk_sized, (p.qty, risk_sized)                         # never above the risk rule
p = prop({**cfg, "forex_size": {"mode": "notional", "usd": 25}}, score=10)
assert not p.ok and "Confidence" in p.why, p.why                        # confidence threshold still applies
tight = json.loads(json.dumps(cfg))
tight["markets"]["forex"]["margin_rate"] = 1e7   # deposit far above the $100,000 practice account
tight["forex_size"] = {"mode": "notional", "usd": 25}
p = prop(tight)
assert not p.ok and "margin" in p.why, p.why
print(f"   agent: risk mode {risk_sized:g} units (no 1,000-unit lots); $25 mode 19 GBP ($24.15); "
      "capped by the risk rule; low score and low margin refused")

# 8. Settings: the fixed amount is validated
assert st.load()["forex_size"] == {"mode": "risk", "usd": 25.0}
markets = list(cfg["markets"])
base = {"mode": "manual", "check": {"enabled": False, "time": "08:00"}, "auto": {"markets": [], "methods": []}}
st.save({**base, "forex_size": {"mode": "notional", "usd": 10}}, markets)
assert st.load()["forex_size"] == {"mode": "notional", "usd": 10.0}
for bad in ({"mode": "notional", "usd": 0}, {"mode": "notional", "usd": "ten"}, {"mode": "lots", "usd": 5}):
    try:
        st.save({**base, "forex_size": bad}, markets)
        raise AssertionError(f"accepted {bad}")
    except ValueError:
        pass
print("   Settings: $10 fixed size saved; $0, text and unknown modes refused")


# 9. OANDA: its own size rules and free margin, the units it is sent, and what happens when it fails
class Resp:
    def __init__(self, code, body):
        self.status_code, self._body, self.content, self.text = code, body, b"x", json.dumps(body)

    def json(self):
        return self._body


class FakeOanda:
    def __init__(self, fail_instruments=False):
        self.fail, self.sent, self.headers = fail_instruments, [], {}

    def request(self, method, url, **kw):
        self.sent.append((method, url, kw.get("json")))
        if url.endswith("/summary"):
            return Resp(200, {"account": {"currency": "USD", "balance": "1000", "NAV": "1000", "marginAvailable": "0.40"}})
        if url.endswith("/instruments"):
            if self.fail:
                return Resp(503, {"errorMessage": "service unavailable"})
            return Resp(200, {"instruments": [{"name": "EUR_USD", "minimumTradeSize": "1", "tradeUnitsPrecision": 0,
                                               "maximumOrderUnits": "100000000", "marginRate": "0.02"}]})
        if url.endswith("/openPositions"):
            return Resp(200, {"positions": []})
        if url.endswith("/orders") and method == "POST":
            return Resp(201, {"orderFillTransaction": {"units": kw["json"]["order"]["units"], "price": "1.0850"}})
        return Resp(200, {})

    def get(self, url, **kw):
        return self.request("GET", url, **kw)


os.environ["OANDA_TOKEN"] = os.environ.get("OANDA_TOKEN") or "test-token"
os.environ["OANDA_ACCOUNT_ID"] = os.environ.get("OANDA_ACCOUNT_ID") or "101-000-0000000-000"
fake = FakeOanda()
o = oa.OandaPractice("forex", session=fake)
sp = fs.spec_for(o, "EURUSD=X", {})
assert sp.margin_rate == Decimal("0.02") and sp.min_units == 1 and sp.precision == 0
assert fs.margin_available(o, 1000, {}, {}, lambda s: sp) == Decimal("0.40")
pl = fs.plan(fs.units_for_usd(25, 1.085, 1.0, sp), 1.085, 1.075, 1.0, sp, {})
assert pl.margin > Decimal("0.40")        # $25 at 2% = $0.46 > $0.40 free: refused before sending
o.open("EURUSD=X", 1, 9, 1.085, 1.075, "Your trade", 0)
order = next(j for m, u, j in fake.sent if m == "POST")["order"]
assert order["units"] == "9" and order["clientExtensions"]["id"].startswith("ta"), order
print("   OANDA: reads minimum size, unit precision and margin rate; sends '9' units with its order tag")
down = oa.OandaPractice("forex", session=FakeOanda(True))
assert fs.spec_for(down, "EURUSD=X", {"margin_rate": 0.0333}) == fs.Spec(margin_rate=Decimal("0.0333"))
print("   OANDA unavailable: falls back to cautious practice rules instead of crashing")

shutil.rmtree(tmp, ignore_errors=True)
print("forex small-trade test passed")
