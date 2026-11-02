"""Exercise the dashboard's approve / close flow on a throwaway local paper account.

Run: python tests/dashboard_test.py
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import trader.agent as ag
import trader.broker as br
from trader.dashboard.server import App
from trader.live import BrokerError

tmp = Path(__file__).parent / ".dashtest"
shutil.rmtree(tmp, ignore_errors=True)
br.STATE_DIR = tmp
import trader.dashboard.server as srv
import trader.performance as perf
srv.STATE_DIR = tmp
perf.STATE_DIR = tmp
perf.SNAPSHOTS = tmp / "account_history.json"

real_load = ag.load_config
def test_config():
    cfg = real_load()
    cfg["risk"]["min_score"] = 20
    for m in cfg["markets"].values():
        m["broker"] = "paper"
    cfg["markets"] = {k: v for k, v in cfg["markets"].items() if k in ("forex", "crypto")}
    return cfg
ag.load_config = test_config

app = App()
app._scan()
view = app.ideas_view()
ok = [i for i in view["ideas"] if i["proposal"]["ok"]]
print("ideas:", len(view["ideas"]), "approvable:", len(ok))
first = ok[0]
print("approve ->", app.approve(first["id"]))
try:
    app.approve(first["id"])
    raise AssertionError("second approval of the same idea must be refused")
except BrokerError as e:
    print("approve again ->", e)
acct = next(a for a in app.overview()["accounts"] if a["market"] == first["market"])
pos = acct["positions"][0]
print("open trade:", pos["side"], pos["qty"], pos["name"], "stop", round(pos["stop"], 4), "P&L", round(pos["pnl"], 2))
print("close ->", app.close(first["market"], first["symbol"]))
print("history:", [(h["action"], h["name"]) for h in app.history()])
practice = app.results()["practice"]
t = practice["trades"][0]
assert practice["count"] == 1 and t["method"] and t["return_pct"] is not None
print(f"results: {practice['count']} closed trade, P&L {t['pnl']:+.2f} ({t['return_pct']:+.2f}%), method {t['method']}")
assert perf.SNAPSHOTS.exists(), "account values should be recorded"
app.set_paused(True)
print("paused ->", app.ideas_view()["ideas"][1]["proposal"]["why"])
app.set_paused(False)
shutil.rmtree(tmp)
print("dashboard test passed")
