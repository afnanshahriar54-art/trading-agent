"""Open the Trading Agent in its own window, like a normal Windows program.

Starts the dashboard server in the background (no console window), opens it in an
app-style Edge or Chrome window, and stops the server a few minutes after the
window is closed (the page sends a heartbeat while it is open).
Run with pythonw.exe -m trader.desktop, or the bundled TradingAgent.exe.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from .dashboard import server as dash
from .paths import HOME

URL = f"http://{dash.HOST}:{dash.PORT}"
STALE_AFTER = 180   # seconds without a heartbeat before the server stops (minimised windows ping slowly)
FIRST_PING_WAIT = 90


def already_running() -> bool:
    try:
        with urllib.request.urlopen(URL + "/api/ping", timeout=1):
            return True
    except OSError:
        return False


def find_browser() -> str | None:
    """Edge ships with Windows 10/11; Chrome is the fallback. Both support app windows."""
    roots = [os.environ.get(v) for v in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
        for root in filter(None, roots):
            p = Path(root) / rel
            if p.exists():
                return str(p)
    return shutil.which("msedge") or shutil.which("chrome")


def first_run_setup() -> None:
    """An installed copy starts with only the defaults; create the files it needs."""
    HOME.mkdir(parents=True, exist_ok=True)
    env, example = HOME / ".env", HOME / ".env.example"
    if not env.exists() and example.exists():
        shutil.copyfile(example, env)


def open_window() -> None:
    browser = find_browser()
    if browser:
        subprocess.Popen([browser, f"--app={URL}", f"--user-data-dir={HOME / '.app_window'}",
                          "--window-size=1200,860", "--no-first-run", "--no-default-browser-check"])
    else:
        webbrowser.open(URL)


def main() -> None:
    if sys.stdout is None:  # pythonw / windowed .exe: keep a log instead of a console
        log = open(HOME / "app.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
    first_run_setup()
    if "--daily-check" in sys.argv:  # started by Windows Task Scheduler
        from . import daily
        daily.main()
        return

    if already_running():  # a second launch just opens another window on the running app
        open_window()
        return

    dash.Handler.app = app = dash.App()
    app.start_watcher()  # take-profit and safety net, live, while the app is open
    httpd = dash.Server((dash.HOST, dash.PORT), dash.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"{time.ctime()}: Trading Agent started at {URL}")
    if "--no-window" not in sys.argv:  # --no-window is for automated tests
        open_window()

    started = time.time()
    while True:
        time.sleep(5)
        seen = app.last_seen
        if (seen and time.time() - seen > STALE_AFTER) or (not seen and time.time() - started > FIRST_PING_WAIT):
            break
    httpd.shutdown()
    print(f"{time.ctime()}: window closed, Trading Agent stopped")


if __name__ == "__main__":
    main()
