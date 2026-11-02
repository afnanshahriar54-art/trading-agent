"""Track record of trade ideas: every idea the app suggests is written down, then checked
against what the price really did afterwards, whether you took the trade or not.

Each idea is followed for up to 20 trading days (30 days for crypto) as if it had been
taken at the price shown, with its safety net (and break-even / trailing, if on). The
take-profit is included when it's set as a multiple of the risk or a percent; a fixed
dollar take-profit depends on the trade size, so it's left out here.
"""
from __future__ import annotations

import datetime as dt
import json

import numpy as np

from . import data, names, odds
from .paths import HOME
from .risk import RiskConfig, RiskManager

LOG_FILE = HOME / "reports" / "idea_log.jsonl"
REPEAT_DAYS = 7   # the same idea showing again within a week counts once


def _read() -> list[dict]:
    if not LOG_FILE.exists():
        return []
    rows = []
    for line in LOG_FILE.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def record(cands: list, cfg: dict) -> int:
    """Write down new ideas from a scan. Returns how many were new."""
    rm = RiskManager(RiskConfig(**cfg["risk"]))
    last_seen: dict[tuple, dt.date] = {}
    for r in _read():
        key, day = (r["market"], r["symbol"], r["side"]), dt.date.fromisoformat(r["date"])
        last_seen[key] = max(last_seen.get(key, day), day)
    new = []
    for c in cands:
        a, key = c.analysis, (c.market, c.symbol, c.side)
        try:
            day = data.load(c.symbol, c.market, cfg["history_years"]).index[-1].date()
        except Exception:
            continue
        if key in last_seen and (day - last_seen[key]).days < REPEAT_DAYS:
            continue
        stop = rm.stop_price(a.price, a.atr, c.side)
        mode = (cfg["risk"].get("take_profit") or {}).get("mode")
        target = rm.target_price(a.price, stop, c.side, 0) if mode in ("risk", "percent") else None
        new.append({"date": day.isoformat(), "market": c.market, "symbol": c.symbol, "side": c.side,
                    "method": c.view.strategy.name, "score": round(c.score), "price": a.price,
                    "stop": stop, "target": target, "strong": c.score >= cfg["risk"]["min_score"]})
        last_seen[key] = day
    if new:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.writelines(json.dumps(r) + "\n" for r in new)
    return len(new)


def follow(row: dict, df, protect: dict | None) -> dict:
    """What happened to one idea since it was suggested."""
    days = odds.DAYS.get(row["market"], odds.DEFAULT_DAYS)
    after = df[df.index.date > dt.date.fromisoformat(row["date"])].iloc[:days]
    if after.empty:
        return {"status": "waiting"}
    bars = after[["open", "high", "low", "close"]].astype(float).to_numpy()
    prev = np.concatenate([[row["price"]], bars[:-1, 3]])
    w = odds.walk(row["price"], row["stop"], row.get("target"), row["side"], (bars / prev[:, None])[None], protect)
    ended, exit_px = str(w["ended"][0]), float(w["exit"][0])
    finished = ended != "open" or len(after) >= days
    return {"status": ("target" if ended == "target" else "stop" if ended == "stop" else "time") if finished else "running",
            "result": row["side"] * (exit_px / row["price"] - 1),
            "result_r": row["side"] * (exit_px - row["price"]) / abs(row["price"] - row["stop"]),
            "hold": float(after.close.iloc[int(w["day"][0]) - 1] / row["price"] - 1),
            "days": int(w["day"][0]) if ended != "open" else len(after)}


def review(cfg: dict) -> dict:
    """Every logged idea with its outcome so far, plus the totals for finished ones."""
    rows, frames, out = _read(), {}, []
    for r in rows:
        key = (r["symbol"], r["market"])
        if key not in frames:
            try:
                frames[key] = data.load(r["symbol"], r["market"], cfg["history_years"])
            except Exception:
                frames[key] = None
        f = follow(r, frames[key], cfg.get("protect")) if frames[key] is not None else {"status": "waiting"}
        out.append({**r, **f, "name": names.symbol(r["symbol"]), "market_name": names.market(r["market"])})
    done = [r for r in out if r["status"] in ("target", "stop", "time")]
    return {"ideas": out[::-1][:60], "count": len(out), "running": sum(r["status"] == "running" for r in out),
            "all": _totals(done), "strong": _totals([r for r in done if r.get("strong")]),
            "markets": {names.market(m): _totals([r for r in done if r["market"] == m])
                        for m in dict.fromkeys(r["market"] for r in done)},
            "since": rows[0]["date"] if rows else None}


def _totals(done: list[dict]) -> dict:
    if not done:
        return {"finished": 0}
    res = np.array([r["result"] for r in done])
    return {"finished": len(done), "win_rate": float((res > 0).mean()), "avg": float(res.mean()),
            "avg_r": float(np.mean([r["result_r"] for r in done])),
            "hold": float(np.mean([r["hold"] for r in done]))}
