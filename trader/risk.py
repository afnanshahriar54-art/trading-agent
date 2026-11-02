"""Risk manager: position sizing, exposure limits, daily loss cap and kill switch.

The risk manager has the final say; no order reaches a broker without passing it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .paths import HOME

KILL_SWITCH = HOME / "KILL_SWITCH"


@dataclass
class RiskConfig:
    risk_per_trade_pct: float = 1.0
    max_position_pct: float = 20.0
    max_open_positions: int = 8
    daily_loss_limit_pct: float = 3.0
    atr_stop_multiple: float = 2.5
    min_score: float = 55
    starting_cash: float = 100_000
    take_profit: dict | None = None  # {"mode": "risk"|"percent"|"amount"|"off", "value": float}

    def __post_init__(self):
        """Refuse settings that would make the limits meaningless (Settings also checks them on save)."""
        checks = [("risk_per_trade_pct", 0, 100), ("max_position_pct", 0, 1000), ("max_open_positions", 1, 1000),
                  ("daily_loss_limit_pct", 0, 100), ("atr_stop_multiple", 0, 100), ("min_score", 0, 100),
                  ("starting_cash", 0, 1e12)]
        for name, lo, hi in checks:
            v = getattr(self, name)
            if not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi or (v == 0 and lo == 0
                                                                                              and name != "min_score"):
                raise ValueError(f"Risk setting {name} = {v!r} is outside the sensible range ({lo} to {hi}).")


def order_problem(side: int, qty: float, price: float, stop: float, fx: float = 1.0,
                  target: float | None = None) -> str | None:
    """The last check before any order: a broken price feed or a bug must never place a trade.
    Returns what's wrong, in words, or None if the order makes sense."""
    nums = {"price": price, "quantity": qty, "safety net": stop, "currency rate": fx}
    for name, v in nums.items():
        if not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
            return f"the {name} ({v!r}) isn't a valid positive number"
    if side not in (1, -1):
        return f"unknown direction {side!r}"
    if side * (price - stop) <= 0:
        return f"the safety net ({stop:.6g}) is on the wrong side of the price ({price:.6g})"
    if target is not None and (not math.isfinite(target) or side * (target - price) <= 0):
        return f"the take-profit ({target!r}) is on the wrong side of the price ({price:.6g})"
    return None


class RiskManager:
    def __init__(self, cfg: RiskConfig):
        self.cfg = cfg

    def stop_price(self, entry: float, atr: float, side: int) -> float:
        return entry - side * self.cfg.atr_stop_multiple * atr

    def target_price(self, entry: float, stop: float, side: int, qty: float, fx: float = 1.0) -> float | None:
        """Take-profit price: where a winning trade is closed. None when switched off."""
        tp = self.cfg.take_profit or {"mode": "off"}
        mode, value = tp.get("mode"), float(tp.get("value") or 0)
        if mode == "risk":
            return entry + side * value * abs(entry - stop)
        if mode == "percent":
            return entry * (1 + side * value / 100)
        if mode == "amount" and qty > 0 and fx > 0:
            return entry + side * value / (qty * fx)
        return None

    def size(self, equity: float, entry: float, stop: float, fx: float = 1.0) -> float:
        """Quantity so that hitting the stop loses at most risk_per_trade_pct of equity,
        capped by max_position_pct. `fx` converts prices into the account currency
        (forex pairs are priced in their quote currency, e.g. yen for USDJPY)."""
        if not all(math.isfinite(v) for v in (equity, entry, stop, fx)) or equity <= 0 or fx <= 0:
            return 0.0  # broken numbers: size nothing
        per_unit_risk = abs(entry - stop) * fx
        if per_unit_risk <= 0 or entry <= 0:
            return 0.0
        by_risk = equity * self.cfg.risk_per_trade_pct / 100 / per_unit_risk
        by_cap = equity * self.cfg.max_position_pct / 100 / (entry * fx)
        return max(min(by_risk, by_cap), 0.0)

    def check_new_trade(self, score: float, open_positions: int, equity: float,
                        day_start_equity: float, cash: float, cost: float,
                        ignore_pause: bool = False, manual: bool = False) -> tuple[bool, str]:
        """ignore_pause is for the historical replay, which must not depend on today's pause switch.
        manual is for trades you pick yourself: they have no confidence score, every other rule applies."""
        if KILL_SWITCH.exists() and not ignore_pause:
            return False, "Trading is paused (the stop switch is on)."
        if not all(math.isfinite(v) for v in (score, equity, day_start_equity, cash, cost)) or equity <= 0:
            return False, "The account or price numbers look broken right now, so no trade. Try again shortly."
        if score < self.cfg.min_score and not manual:
            return False, f"Confidence {score:.0f} is below your minimum of {self.cfg.min_score:.0f}."
        if open_positions >= self.cfg.max_open_positions:
            return False, f"You already have the maximum of {self.cfg.max_open_positions} open trades in this account."
        day_pnl_pct = (equity / day_start_equity - 1) * 100 if day_start_equity else 0
        if day_pnl_pct <= -self.cfg.daily_loss_limit_pct:
            return False, f"This account is down {-day_pnl_pct:.1f}% today, past the {self.cfg.daily_loss_limit_pct:g}% daily limit."
        if cost > cash:
            return False, f"Not enough cash: {cost:,.0f} needed, {cash:,.0f} available."
        return True, "ok"
