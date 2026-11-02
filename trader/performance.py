"""Your practice results: closed trades and account value over time."""
from __future__ import annotations

import datetime as dt
import json
import re

from . import data, names
from .agent import MANUAL
from .broker import STATE_DIR
from .paths import HOME

SNAPSHOTS = HOME / "reports" / "account_history.json"
_PNL_IN_NOTE = re.compile(r"P&L ~?([+-][\d,]+\.?\d*)")


def record_snapshot(values: dict[str, float]) -> None:
    """Remember each account's value once per day (the latest check of the day wins)."""
    hist = json.loads(SNAPSHOTS.read_text()) if SNAPSHOTS.exists() else {}
    hist[dt.date.today().isoformat()] = values
    SNAPSHOTS.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOTS.write_text(json.dumps(hist, indent=1))


def closed_trades() -> list[dict]:
    rows = []
    for f in STATE_DIR.glob("*.json") if STATE_DIR.exists() else []:
        market = next((m for m in names.MARKETS if f.stem.startswith(m)), f.stem)
        for t in json.loads(f.read_text()).get("trades", []):
            if t["action"] != "CLOSE":
                continue
            pnl = t.get("pnl")
            if pnl is None:  # trades closed before details were recorded
                m = _PNL_IN_NOTE.search(t.get("note", ""))
                pnl = float(m.group(1).replace(",", "")) if m else 0.0
            cost = t.get("cost") or 0
            rows.append({"time": t["time"], "market": market, "market_name": names.market(market),
                         "symbol": t["symbol"], "name": names.symbol(t["symbol"]), "method": t.get("strategy"),
                         "pnl": pnl, "return_pct": pnl / cost * 100 if cost else None,
                         "opened": t.get("opened"), "reason": t.get("note", "").split(";")[0],
                         "currency": "BDT" if market == "dse" else "USD"})
    rows.sort(key=lambda r: r["time"], reverse=True)
    return rows


def summary() -> dict:
    trades = closed_trades()
    usd = [t for t in trades if t["currency"] == "USD"]
    wins = [t["pnl"] for t in usd if t["pnl"] > 0]
    losses = [t["pnl"] for t in usd if t["pnl"] <= 0]
    hist = json.loads(SNAPSHOTS.read_text()) if SNAPSHOTS.exists() else {}
    days = sorted(hist)
    # Compare with the S&P 500 over the same days.
    sp = {}
    if len(days) > 1:
        try:
            close = data.load("SPY", "us_stocks").close
            for d in days:
                c = close[close.index <= d]
                if len(c):
                    sp[d] = float(c.iloc[-1])
        except Exception:
            sp = {}
    by_who = {}
    for who, rows in (("The agent's ideas", [t for t in usd if t["method"] != MANUAL]),
                      ("Your own trades", [t for t in usd if t["method"] == MANUAL])):
        w = [t["pnl"] for t in rows if t["pnl"] > 0]
        by_who[who] = {"trades": len(rows), "win_rate": len(w) / len(rows) if rows else None,
                       "pnl": sum(t["pnl"] for t in rows)}
    markets = sorted({m for d in days for m in hist[d]})
    series = {m: [{"d": d, "v": hist[d][m]} for d in days if m in hist[d]] for m in markets}
    return {
        "trades": trades[:100], "count": len(trades),
        "win_rate": len(wins) / len(usd) if usd else None,
        "total_pnl_usd": sum(t["pnl"] for t in usd),
        "total_pnl_bdt": sum(t["pnl"] for t in trades if t["currency"] == "BDT"),
        "avg_win": sum(wins) / len(wins) if wins else None,
        "avg_loss": sum(losses) / len(losses) if losses else None,
        "since": days[0] if days else None,
        "by_who": by_who,
        "accounts": {m: {"name": names.market(m), "points": series[m]} for m in markets},
        "sp500": [{"d": d, "v": v} for d, v in sp.items()],
    }
