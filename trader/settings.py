"""Your choices from the Settings page: trading mode, auto-mode rules, safety rules, daily check.

Kept in settings.json next to config.yaml. Safety rules saved here override the
defaults in config.yaml (see agent.load_config).
"""
from __future__ import annotations

import datetime as dt
import json

from .paths import HOME
from .strategies import STRATEGIES

FILE = HOME / "settings.json"

# Allowed range for each safety rule you can change in the app.
RULE_LIMITS = {
    "min_score": (30, 95),
    "risk_per_trade_pct": (0.1, 3.0),
    "max_position_pct": (1.0, 50.0),
    "max_open_positions": (1, 20),
    "daily_loss_limit_pct": (0.5, 20.0),
}

# The original seven methods start switched on; newer ones start off until you've seen their test results.
DEFAULT_ENABLED = [s.name for s in STRATEGIES[:7]]

DEFAULTS = {
    "mode": "manual",                            # manual: you approve every trade; auto: the app follows your rules
    "check": {"enabled": False, "time": "08:00"},
    # Forex is off by default: the report card showed the methods losing money there.
    "auto": {"markets": ["us_stocks", "crypto"], "methods": DEFAULT_ENABLED},
    "rules": {},
    # enabled: names of switched-on methods (None = the defaults); custom: methods you built.
    "methods": {"enabled": None, "custom": []},
    # Close a winning trade automatically. mode: "risk" (value x what the trade risked),
    # "percent" (value % gain), "amount" (value in account money), or "off".
    "take_profit": {"mode": "risk", "value": 2.0},
    # Move the safety net in your favour as a trade gains (both measured in "R" = what the trade risked).
    # breakeven: once up `at` x R, the safety net moves to the buy price.
    # trailing: the safety net follows the best price, staying `distance` x R behind it.
    "protect": {"breakeven": {"on": False, "at": 1.0}, "trailing": {"on": False, "distance": 1.5}},
    # How the agent sizes forex trades. "risk": by the 1% risk rule (as before). "notional": a fixed
    # dollar size per trade (e.g. $25), still never more than the risk rule allows.
    "forex_size": {"mode": "risk", "usd": 25.0},
}
FOREX_USD_LIMITS = (1.0, 1_000_000.0)
PROTECT_LIMITS = {"at": (0.25, 5.0), "distance": (0.25, 5.0)}

TAKE_PROFIT_LIMITS = {"risk": (0.5, 10.0), "percent": (0.5, 100.0), "amount": (1.0, 1_000_000.0)}


def load() -> dict:
    saved = json.loads(FILE.read_text()) if FILE.exists() else {}
    if "daily_check" in saved and "check" not in saved:  # settings from the earlier version
        old = saved["daily_check"]
        saved["check"] = {"enabled": old.get("enabled", False), "time": old.get("time", "08:00")}
        saved["mode"] = "auto" if old.get("auto_trade") else "manual"
    return {
        "mode": saved.get("mode", DEFAULTS["mode"]),
        "check": {**DEFAULTS["check"], **saved.get("check", {})},
        "auto": {**DEFAULTS["auto"], **saved.get("auto", {})},
        "rules": saved.get("rules", {}),
        "methods": {**DEFAULTS["methods"], **saved.get("methods", {})},
        "take_profit": {**DEFAULTS["take_profit"], **saved.get("take_profit", {})},
        "protect": {k: {**DEFAULTS["protect"][k], **saved.get("protect", {}).get(k, {})} for k in DEFAULTS["protect"]},
        "forex_size": {**DEFAULTS["forex_size"], **saved.get("forex_size", {})},
    }


def enabled_methods(s: dict | None = None) -> list[str]:
    s = s or load()
    return s["methods"]["enabled"] if s["methods"]["enabled"] is not None else DEFAULT_ENABLED


def save_methods(enabled: list[str], custom: list[dict]) -> None:
    s = load()
    s["methods"] = {"enabled": enabled, "custom": custom}
    FILE.write_text(json.dumps(s, indent=2))


def save(new: dict, markets: list[str]) -> dict:
    """Validate and store. Raises ValueError with a plain-language message."""
    s = load()
    if new.get("mode") not in ("manual", "auto"):
        raise ValueError("Choose Manual or Auto mode.")
    s["mode"] = new["mode"]
    check = new.get("check", {})
    try:
        dt.datetime.strptime(check.get("time", "08:00"), "%H:%M")
    except ValueError:
        raise ValueError("Enter the check time like 08:00.")
    s["check"] = {"enabled": bool(check.get("enabled")), "time": check.get("time", "08:00")}
    auto = new.get("auto", {})
    method_names = {m.name for m in STRATEGIES} | {m["name"] for m in s["methods"]["custom"]}
    s["auto"] = {"markets": [m for m in auto.get("markets", []) if m in markets],
                 "methods": [m for m in auto.get("methods", []) if m in method_names]}
    if s["mode"] == "auto":
        if not s["auto"]["markets"]:
            raise ValueError("Auto mode needs at least one market.")
        if not s["auto"]["methods"]:
            raise ValueError("Auto mode needs at least one method.")
        if not s["check"]["enabled"]:
            raise ValueError("Auto mode trades during the daily check, so turn the daily check on.")
    rules = {}
    for key, (lo, hi) in RULE_LIMITS.items():
        if key in new.get("rules", {}):
            v = float(new["rules"][key])
            if not lo <= v <= hi:
                raise ValueError(f"{RULE_NAMES[key]} must be between {lo:g} and {hi:g}.")
            rules[key] = int(v) if key == "max_open_positions" else v
    s["rules"] = rules
    tp = new.get("take_profit", s["take_profit"])
    if tp.get("mode") not in ("risk", "percent", "amount", "off"):
        raise ValueError("Choose when to take profit.")
    value = s["take_profit"]["value"]
    if tp["mode"] != "off":
        try:
            value = float(tp.get("value"))
        except (TypeError, ValueError):
            raise ValueError("Enter a number for the take-profit target.")
        lo, hi = TAKE_PROFIT_LIMITS[tp["mode"]]
        if not lo <= value <= hi:
            raise ValueError(f"The take-profit target must be between {lo:g} and {hi:g}.")
    s["take_profit"] = {"mode": tp["mode"], "value": value}
    pr = new.get("protect", s["protect"])
    out = {}
    for key, field in (("breakeven", "at"), ("trailing", "distance")):
        item = pr.get(key, s["protect"][key])
        try:
            v = float(item.get(field, s["protect"][key][field]))
        except (TypeError, ValueError):
            raise ValueError("Enter a number for the profit protection.")
        lo, hi = PROTECT_LIMITS[field]
        if not lo <= v <= hi:
            raise ValueError(f"The {'break-even' if key == 'breakeven' else 'trailing'} setting must be between {lo:g} and {hi:g}.")
        out[key] = {"on": bool(item.get("on")), field: v}
    s["protect"] = out
    fs = new.get("forex_size", s["forex_size"])
    if fs.get("mode") not in ("risk", "notional"):
        raise ValueError("Choose how to size forex trades.")
    try:
        usd = float(fs.get("usd", s["forex_size"]["usd"]))
    except (TypeError, ValueError):
        raise ValueError("Enter a dollar amount for forex trades.")
    lo, hi = FOREX_USD_LIMITS
    if not lo <= usd <= hi:
        raise ValueError(f"The forex trade amount must be between ${lo:g} and ${hi:,.0f}.")
    s["forex_size"] = {"mode": fs["mode"], "usd": usd}
    FILE.write_text(json.dumps(s, indent=2))
    return s


RULE_NAMES = {
    "min_score": "Minimum confidence",
    "risk_per_trade_pct": "Most a trade can lose",
    "max_position_pct": "Biggest single trade",
    "max_open_positions": "Most open trades",
    "daily_loss_limit_pct": "Daily loss limit",
}
