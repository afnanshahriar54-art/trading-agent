"""End-to-end check of the trading path on a throwaway paper account.

Run: python tests/smoke_test.py
Lowers the minimum score so trades actually happen, answers the review prompts
automatically, then simulates a strategy flip to check that exits work.
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import trader.agent as ag
import trader.broker as br

tmp = Path(__file__).parent / ".smoke"
shutil.rmtree(tmp, ignore_errors=True)
br.STATE_DIR = tmp / "paper"
ag.REPORT_DIR = tmp / "reports"

cfg = ag.load_config()
cfg["risk"]["min_score"] = 20


def show(path):
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(("- ", "Equity")):
            print("   ", line)


answers = iter(["y", "n"] + ["y"] * 30)  # approve all but the second idea
print("1) Review mode, answering y / n / y:")
show(ag.run(cfg, "review", ["forex", "crypto"], ask=lambda q: next(answers, "n")))

# Flip a position in whichever market got one today.
market = next(m for m in ("forex", "crypto") if json.loads((br.STATE_DIR / f"{m}.json").read_text())["positions"])
state_file = br.STATE_DIR / f"{market}.json"
state = json.loads(state_file.read_text())
sym = next(iter(state["positions"]))
state["positions"][sym]["qty"] *= -1  # pretend we hold the opposite side, so the signal disagrees
state_file.write_text(json.dumps(state))
print(f"2) Auto mode after flipping {sym} (expect it to be CLOSED):")
show(ag.run(cfg, "auto", [market]))

shutil.rmtree(tmp)
