"""Honest test of the Kronos AI forecast on prices it never saw.

Kronos was published in August 2025, so its training data ends before that. This test
starts in November 2025: every week, for every asset, it forecasts the next 5 days using
only earlier prices, then compares with what really happened. Two simple baselines show
whether the AI adds anything: "always guess up" and "last week's direction continues".
"""
from __future__ import annotations

import datetime as dt
import json

import numpy as np

from . import data, forecast, names
from .paths import HOME

RESULT_FILE = HOME / "reports" / "ai_test.json"


def run(cfg: dict, start: str = "2025-11-01", horizon: int = 5, step: int = 5, paths: int = 10,
        progress=None, symbols: dict[str, list[str]] | None = None) -> dict:
    """symbols: {market: [symbols]} to test only some assets (the model is slow on a normal PC)."""
    rows = []
    for market, mcfg in cfg["markets"].items():
        if symbols is not None:
            if market not in symbols:
                continue
            mcfg = {**mcfg, "symbols": symbols[market]}
        cost = 2 * (mcfg["fee_bps"] + mcfg["slippage_bps"]) / 10_000  # in and out
        items_by_round: dict[int, list] = {}
        for sym in mcfg["symbols"]:
            try:
                df = data.load(sym, market, cfg["history_years"])
            except Exception:
                continue
            first = int(np.searchsorted(df.index, np.datetime64(start)))
            first = max(first, forecast.LOOKBACK)
            for r, i in enumerate(range(first, len(df) - horizon, step)):
                items_by_round.setdefault(r, []).append((sym, df, i))
        for r, items in sorted(items_by_round.items()):
            if progress:
                progress(f"{names.market(market)}: week {r + 1} of {len(items_by_round)}")
            batch = [(f"{sym}|{i}", df.iloc[:i + 1], market) for sym, df, i in items]
            fc = forecast.forecast_many(batch, horizon=horizon, paths=paths, seed=r)
            for sym, df, i in items:
                f = fc.get(f"{sym}|{i}")
                if f is None:
                    continue
                actual = float(df.close.iloc[i + horizon] / df.close.iloc[i] - 1)
                before = float(df.close.iloc[i] / df.close.iloc[i - horizon] - 1)
                rows.append({"market": market, "symbol": sym, "date": df.index[i].date().isoformat(),
                             "up_paths": f["up_paths"], "paths": f["paths"], "exp": f["exp_return"],
                             "actual": actual, "before": before, "cost": cost,
                             "short_ok": bool(mcfg["allow_short"])})
    out = {"created": dt.datetime.now().strftime("%d %b %Y, %I:%M %p"), "start": start, "horizon": horizon,
           "paths": paths, "markets": {m: _summary([r for r in rows if r["market"] == m]) for m in cfg["markets"]
                                       if any(r["market"] == m for r in rows)},
           "assets": sorted({r["symbol"] for r in rows}),
           "all": _summary(rows)}
    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def _summary(rows: list[dict]) -> dict:
    if not rows:
        return {"forecasts": 0}
    exp = np.array([r["exp"] for r in rows])
    act = np.array([r["actual"] for r in rows])
    up = np.array([r["up_paths"] / r["paths"] for r in rows])
    before = np.array([r["before"] for r in rows])
    hit = lambda guess, mask=None: float(np.mean((np.sign(guess) == np.sign(act))[mask if mask is not None else slice(None)]))
    sure = (up >= 0.8) | (up <= 0.2)
    # Simple strategy: buy when 7+ of 10 paths end higher; sell short (where allowed) when 3 or fewer do.
    trades = []
    for r, u in zip(rows, up):
        side = 1 if u >= 0.7 else (-1 if u <= 0.3 and r["short_ok"] else 0)
        if side:
            trades.append(side * r["actual"] - r["cost"])
    return {
        "forecasts": len(rows),
        "ai_hit": hit(exp), "ai_hit_sure": hit(np.where(up >= 0.5, 1, -1), sure) if sure.any() else None,
        "sure_count": int(sure.sum()),
        "always_up_hit": float(np.mean(act > 0)), "momentum_hit": hit(before),
        "correlation": float(np.corrcoef(exp, act)[0, 1]) if len(rows) > 2 else None,
        "trades": len(trades), "avg_trade": float(np.mean(trades)) if trades else None,
        "win_rate": float(np.mean(np.array(trades) > 0)) if trades else None,
        "avg_hold": float(np.mean(act)),  # average 5-day change: what simply holding earned
    }


def load_saved() -> dict | None:
    return json.loads(RESULT_FILE.read_text(encoding="utf-8")) if RESULT_FILE.exists() else None
