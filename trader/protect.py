"""Protect profits: move a trade's safety net in your favour as the trade gains.

R = what the trade risked per unit (the distance from the buy price to the first safety net).
- Break-even: once the best price so far is `at` x R in profit, the safety net moves to the buy price.
- Trailing: the safety net follows the best price so far, staying `distance` x R behind it.
A safety net only ever moves in your favour, never back.
"""
from __future__ import annotations

from . import names


def price_exit(pos: dict, price: float) -> str | None:
    """Safety net or take-profit reached at this price? (Used by the agent and the live watcher.)"""
    side = 1 if pos["qty"] > 0 else -1
    stop, target = pos.get("stop"), pos.get("target")
    if stop and ((side > 0 and price <= stop) or (side < 0 and price >= stop)):
        return f"stop-loss hit at {stop:.4g}"
    if target and ((side > 0 and price >= target) or (side < 0 and price <= target)):
        return f"take-profit reached at {target:.4g}"
    return None


def new_stop(pos: dict, price: float, protect: dict) -> tuple[float, float, float] | None:
    """(new stop, best price so far, R) if the safety net should move, else None."""
    stop = pos.get("stop")
    if not stop or not pos.get("entry"):
        return None
    side = 1 if pos["qty"] > 0 else -1
    entry = pos["entry"]
    r = pos.get("risk_dist") or abs(entry - (pos.get("stop0") or stop))
    if r <= 0:
        return None
    best = pos.get("best") or entry
    best = max(best, price) if side > 0 else min(best, price)
    candidates = [stop]
    be, tr = protect.get("breakeven", {}), protect.get("trailing", {})
    if be.get("on") and side * (best - entry) >= be.get("at", 1.0) * r:
        candidates.append(entry)
    if tr.get("on"):
        candidates.append(best - side * tr.get("distance", 1.5) * r)
    moved = max(candidates) if side > 0 else min(candidates)
    if abs(moved - stop) <= 1e-9 * max(abs(stop), 1) and best == pos.get("best"):
        return None
    return moved, best, r


def manage(broker, prices: dict[str, float], fxs: dict[str, float], protect: dict,
           close_hits: bool = True) -> list[str]:
    """Move safety nets for the latest prices, then close trades whose safety net or take-profit
    was reached (unless the broker itself holds those exits). Returns what happened, in words."""
    events = []
    for sym, pos in list(broker.positions.items()):
        if not pos.get("managed", True) or sym not in prices:
            continue
        price = prices[sym]
        moved = new_stop(pos, price, protect)
        if moved:
            stop, best, r = moved
            if (stop - pos["stop"]) * (1 if pos["qty"] > 0 else -1) > 1e-9:
                events.append(f"{names.symbol(sym)}: safety net moved to {stop:.4g}"
                              + (" (break-even)" if abs(stop - pos["entry"]) < 1e-9 else ""))
            broker.update_stop(sym, stop, best, r)
            pos = broker.positions[sym]
        if close_hits and not getattr(broker, "broker_side_exits", False):
            reason = price_exit(pos, price)
            if reason:
                t = broker.close(sym, price, reason, fxs.get(sym))
                events.append(f"{names.symbol(sym)}: {reason.split(' at ')[0]}, {t['note'].split('; ')[-1]}")
    return events
