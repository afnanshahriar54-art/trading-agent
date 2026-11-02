"""Where the agent keeps its settings and saved data.

- Normal use: this project folder.
- Installed app (bundled .exe): the folder the .exe lives in.
- TRADING_AGENT_HOME, if set, overrides both (used by tests).
"""
import os
import sys
from pathlib import Path

if os.environ.get("TRADING_AGENT_HOME"):
    HOME = Path(os.environ["TRADING_AGENT_HOME"])
elif getattr(sys, "frozen", False):
    HOME = Path(sys.executable).resolve().parent
else:
    HOME = Path(__file__).resolve().parent.parent
