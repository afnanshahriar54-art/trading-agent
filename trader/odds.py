"""What are the odds? A practice run of a trade, 10,000 times, before you place it.

Each run builds a possible next few weeks by picking random real days from this asset's
last two years (how far it jumped at the open, its high, low and close that day). The
trade then lives through those days exactly as the app would handle it: the safety net
(moved by break-even and trailing if they're on) and the take-profit. Counting how the
runs end gives plain odds: "reached take-profit in 38 of 100 runs, safety net in 52".

This is history replayed at random, not a prediction. It knows nothing about news, and it
assumes the trade only ends by its safety net, take-profit, or the time limit.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RUNS = 10_000
YEARS = 2
DAYS = {"crypto": 30}   # calendar days for 24/7 markets; others use 20 trading days (about a month)
DEFAULT_DAYS = 20


def day_moves(df: pd.DataFrame, years: int = YEARS) -> np.ndarray:
    """Each real day as moves from the previous close: open, high, low, close ratios (rows x 4)."""
    d = df[["open", "high", "low", "close"]].astype(float)
    d = d[d.index >= d.index[-1] - pd.Timedelta(days=365 * years)]
    m = d.div(d.close.shift(1), axis=0).iloc[1:].to_numpy()
    return m[np.isfinite(m).all(axis=1) & (m > 0).all(axis=1)]


def walk(entry: float, stop: float, target: float | None, side: int, moves: np.ndarray,
         protect: dict | None = None) -> dict:
    """Live a trade through day moves shaped (runs, days, 4). Returns, per run, how it ended
    ("target", "stop" or "open"), the exit price and the day it ended."""
    runs, days = moves.shape[:2]
    protect = protect or {}
    be, tr = protect.get("breakeven", {}), protect.get("trailing", {})
    r = abs(entry - stop)
    stop_now = np.full(runs, float(stop))
    best = np.full(runs, float(entry))
    prev = np.full(runs, float(entry))
    ended = np.full(runs, "open", dtype="<U6")
    exit_px = np.full(runs, np.nan)
    end_day = np.full(runs, days)
    alive = np.ones(runs, dtype=bool)
    for t in range(days):
        o, h, l, c = (prev * moves[:, t, k] for k in range(4))
        good, bad = (h, l) if side > 0 else (l, h)        # the day's price that helps / hurts this trade
        # Same day both? Count the safety net first: the careful assumption.
        hit_stop = alive & (side * (bad - stop_now) <= 0)
        hit_tp = alive & ~hit_stop & (side * (good - target) >= 0) if target is not None else np.zeros(runs, bool)
        # A gap past a level fills at the open, not at the level.
        exit_px[hit_stop] = np.where(side * (o - stop_now) <= 0, o, stop_now)[hit_stop]
        if target is not None:
            exit_px[hit_tp] = np.where(side * (o - target) >= 0, o, target)[hit_tp]
        ended[hit_stop], ended[hit_tp] = "stop", "target"
        end_day[hit_stop | hit_tp] = t + 1
        alive &= ~(hit_stop | hit_tp)
        # Then the safety net catches up with the best price of the day (it only moves your way).
        best = np.where(alive, _better(best, good, side), best)
        if r > 0:
            if be.get("on"):
                reached = side * (best - entry) >= be.get("at", 1.0) * r
                stop_now = np.where(alive & reached, _better(stop_now, entry, side), stop_now)
            if tr.get("on"):
                stop_now = np.where(alive, _better(stop_now, best - side * tr.get("distance", 1.5) * r, side), stop_now)
        prev = c
    exit_px[alive] = prev[alive]
    return {"ended": ended, "exit": exit_px, "day": end_day}


def _better(a, b, side):
    return np.maximum(a, b) if side > 0 else np.minimum(a, b)


def check(df: pd.DataFrame, market: str, entry: float, stop: float, target: float | None, side: int,
          qty: float, fx: float = 1.0, protect: dict | None = None, runs: int = RUNS, seed: int = 0) -> dict | None:
    """The odds for one planned trade, with money in account currency."""
    moves = day_moves(df)
    if len(moves) < 100 or entry <= 0 or stop == entry or qty <= 0:
        return None
    days = DAYS.get(market, DEFAULT_DAYS)
    rng = np.random.default_rng(seed)
    w = walk(entry, stop, target, side, moves[rng.integers(0, len(moves), size=(runs, days))], protect)
    pnl = side * (w["exit"] - entry) * qty * fx
    risk = abs(entry - stop) * qty * fx
    stopped = w["ended"] == "stop"
    share = lambda mask: round(float(mask.mean()) * 100, 1)
    return {
        "runs": runs, "days": days, "trading_days": market != "crypto",
        "target": share(w["ended"] == "target"),
        "stop_loss": share(stopped & (pnl < 0)),          # safety net hit at a loss
        "stop_profit": share(stopped & (pnl >= 0)),       # safety net hit after it moved up to lock in gains
        "open": share(w["ended"] == "open"),
        "profit_chance": share(pnl > 0),
        "avg": float(pnl.mean()), "good": float(np.percentile(pnl, 90)), "bad": float(np.percentile(pnl, 10)),
        "avg_r": float(pnl.mean() / risk) if risk else None,
    }
