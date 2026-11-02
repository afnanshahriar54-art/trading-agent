"""Daily automatic check, run by Windows Task Scheduler even when the app is closed.

Manual mode: it scans all markets and tells you what it found; you decide.
Auto mode: in the markets you chose, it places the trades that pass your rules (your
methods, your minimum confidence, your safety limits) on the practice accounts and closes
trades whose method or safety net says so. Other markets still get suggestions only.
A Windows notification appears when there is something to look at.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys

from . import agent, data, fx, names, protect, settings
from .paths import HOME
from .risk import RiskConfig, RiskManager

LAST_RUN = HOME / "reports" / "daily_check.json"
TASK_NAME = "Trading Agent daily check"


def _ps(script: str, **env) -> subprocess.CompletedProcess:
    return subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                          capture_output=True, text=True, env={**os.environ, **env},
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def schedule(enabled: bool, at: str) -> None:
    """Create or remove the Windows scheduled task (for this Windows user only)."""
    if not enabled:
        _ps(f"Unregister-ScheduledTask -TaskName '{TASK_NAME}' -Confirm:$false -ErrorAction SilentlyContinue")
        return
    if getattr(sys, "frozen", False):
        exe, args = sys.executable, "--daily-check"
    else:
        exe = str(HOME / ".venv" / "Scripts" / "pythonw.exe")
        args = "-m trader.daily"
    r = _ps(
        "$a = New-ScheduledTaskAction -Execute $env:TA_EXE -Argument $env:TA_ARGS -WorkingDirectory $env:TA_DIR; "
        "$t = New-ScheduledTaskTrigger -Daily -At $env:TA_AT; "
        # StartWhenAvailable: if the PC was off at that time, run as soon as it's back on.
        "$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        "-ExecutionTimeLimit (New-TimeSpan -Minutes 30); "
        f"Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $a -Trigger $t -Settings $s "
        "-Description 'Checks the markets once a day for Trading Agent' -Force | Out-Null",
        TA_EXE=exe, TA_ARGS=args, TA_DIR=str(HOME), TA_AT=at)
    if r.returncode != 0:
        raise RuntimeError(f"Could not set up the daily check: {r.stderr.strip()[:300]}")


def is_scheduled() -> bool:
    return _ps(f"Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction Stop | Out-Null").returncode == 0


def notify(title: str, body: str) -> None:
    """Show a Windows notification (bottom-right of the screen)."""
    _ps("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
        "$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
        "$n = $x.GetElementsByTagName('text'); "
        "$n.Item(0).AppendChild($x.CreateTextNode($env:TA_TITLE)) | Out-Null; "
        "$n.Item(1).AppendChild($x.CreateTextNode($env:TA_BODY)) | Out-Null; "
        "$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe'; "
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show("
        "[Windows.UI.Notifications.ToastNotification]::new($x))",
        TA_TITLE=title, TA_BODY=body)


def check(send_notification: bool = True) -> dict:
    cfg = agent.load_config()
    s = settings.load()
    auto = s["mode"] == "auto"
    auto_markets = [m for m in s["auto"]["markets"] if m in cfg["markets"]] if auto else []
    other_markets = [m for m in cfg["markets"] if m not in auto_markets]
    good, exits, opened, closed, protected = [], [], [], [], []
    if auto_markets:  # auto mode: trade by your rules
        lines = agent.run(cfg, "auto", auto_markets, methods=s["auto"]["methods"]).read_text(encoding="utf-8").splitlines()
        opened = [l[2:] for l in lines if l.startswith(("- OPENED", "- ORDER SENT"))]
        closed = [l[2:] for l in lines if l.startswith("- CLOSED")]
    if other_markets:  # manual mode, or markets left out of auto mode: suggestions only
        analyses, cands = agent.scan(cfg, other_markets, verbose=False)
        rm = RiskManager(RiskConfig(**cfg["risk"]))
        brokers = {}
        for market, mlist in analyses.items():
            try:
                b = brokers[market] = agent.make_broker(market, cfg)
                # Safety nets and take-profits were agreed when each trade opened, so they are
                # enforced here in every mode (with the latest prices); then profits are protected.
                prices, fxs = {}, {}
                for sym in b.positions:
                    try:
                        prices[sym] = float(data.load(sym, market, cfg["history_years"], finished_only=False).close.iloc[-1])
                    except Exception:
                        continue
                    fxs[sym] = fx.quote_to_usd(sym, prices[sym]) if b.margin else 1.0
                for event in protect.manage(b, prices, fxs, cfg.get("protect", {})):
                    (protected if "safety net moved" in event else closed).append(event)
                by_symbol = {a.symbol: a for a in mlist}
                for sym, pos in b.positions.items():
                    a = by_symbol.get(sym)
                    reason = agent.exit_reason(pos, a) if a and pos.get("managed", True) else None
                    if reason:
                        exits.append(f"{names.symbol(sym)}: {reason}")
            except Exception:
                continue
        for c in cands:
            b = brokers.get(c.market)
            if b is None or c.symbol in b.positions or c.symbol in b.pending:
                continue
            a = c.analysis
            if agent.propose(c, b, rm, {c.symbol: a.price}, {c.symbol: a.fx}).ok:
                good.append(f"{'Buy' if c.side > 0 else 'Sell short'} {names.symbol(c.symbol)} ({c.score:.0f}/100)")

    parts = []
    if good:
        parts.append(f"{len(good)} trade idea{'s' if len(good) > 1 else ''} pass your rules: {', '.join(good[:3])}")
    if exits:
        parts.append(f"Consider closing: {'; '.join(exits[:3])}")
    if opened:
        parts.append(f"Placed {len(opened)} practice trade{'s' if len(opened) > 1 else ''}")
    if closed:
        parts.append(f"Closed {len(closed)} practice trade{'s' if len(closed) > 1 else ''}")
    if protected:
        parts.append(f"Protected profits: {'; '.join(protected[:3])}")
    result = {"time": dt.datetime.now().strftime("%d %b %Y, %I:%M %p"), "mode": s["mode"],
              "good": good, "exits": exits, "opened": opened, "closed": closed, "protected": protected,
              "summary": ". ".join(parts) + "." if parts else "Nothing to do today. No idea passed your rules."}
    LAST_RUN.parent.mkdir(parents=True, exist_ok=True)
    LAST_RUN.write_text(json.dumps(result, indent=2), encoding="utf-8")
    if send_notification and parts:
        notify("Trading Agent", result["summary"][:250])
    return result


def last_run() -> dict | None:
    return json.loads(LAST_RUN.read_text(encoding="utf-8")) if LAST_RUN.exists() else None


def main() -> None:
    if sys.stdout is None:  # started by Task Scheduler with no console
        log = open(HOME / "app.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
    try:
        r = check()
        print(f"{r['time']}: daily check: {r['summary']}")
    except Exception as e:
        print(f"{dt.datetime.now()}: daily check failed: {e}")
        notify("Trading Agent", f"The daily check could not run: {e}"[:250])


if __name__ == "__main__":
    main()
