"""Method builder: turns conditions you pick in the app into a working method.

A custom method is stored as plain data, for example:
    {"name": "Cheap in an uptrend", "family": "mean_reversion",
     "entry": [{"type": "price_vs_ma", "op": "above", "n": 200},
               {"type": "rsi", "op": "below", "n": 14, "x": 35}],
     "exit":  [{"type": "rsi", "op": "above", "n": 14, "x": 60}]}
It buys when ALL entry conditions are true, and sells when ANY exit condition is true
(or, with no exit conditions, as soon as the entry conditions stop being true).
Custom methods buy only; they never sell short.
"""
from __future__ import annotations

import re

import pandas as pd

from . import indicators as ind
from .strategies import Strategy, _state

# Each condition: label shown in the app, and its settings with allowed values or (min, max).
CONDITIONS = {
    "price_vs_ma": {"label": "Price is {op} its {n}-day average",
                    "params": {"op": ["above", "below"], "n": (2, 400)}},
    "ma_vs_ma": {"label": "The {a}-day average is {op} the {b}-day average",
                 "params": {"a": (2, 400), "op": ["above", "below"], "b": (2, 400)}},
    "rsi": {"label": "RSI ({n}-day) is {op} {x}",
            "params": {"n": (2, 50), "op": ["above", "below"], "x": (1, 99)}},
    "new_high": {"label": "Price closes at a new {n}-day high", "params": {"n": (5, 252)}},
    "new_low": {"label": "Price closes at a new {n}-day low", "params": {"n": (5, 252)}},
    "macd": {"label": "MACD is {op} its signal line", "params": {"op": ["above", "below"]}},
    "bollinger": {"label": "Price is {op} the {band} Bollinger band",
                  "params": {"op": ["above", "below"], "band": ["upper", "lower"]}},
    "volume": {"label": "Volume is above {x} times its {n}-day average",
               "params": {"x": (1, 10), "n": (5, 100)}},
    "change": {"label": "Price changed {op} {x}% over {n} days",
               "params": {"op": ["more than", "less than"], "x": (-90, 500), "n": (1, 252)}},
    "adx": {"label": "Trend strength (ADX) is {op} {x}",
            "params": {"op": ["above", "below"], "x": (5, 80)}},
}
PARAM_NAMES = {"n": "number of days", "a": "fast average days", "b": "slow average days", "x": "value"}
FAMILIES = {"trend": "Trend-following", "mean_reversion": "Buy-the-dip", "breakout": "Breakout"}
MAX_CONDITIONS = 5


def describe(c: dict) -> str:
    nice = {k: (f"{v:g}" if isinstance(v, float) else v) for k, v in c.items()}  # 35.0 -> 35
    return CONDITIONS[c["type"]]["label"].format(**nice)


def _plain(label: str) -> str:
    return re.sub(r"\{\w+\}", "…", label)


def _check(c: dict) -> dict:
    """Validate one condition; returns a clean copy. Raises ValueError in plain language."""
    spec = CONDITIONS.get(c.get("type"))
    if spec is None:
        raise ValueError("Unknown condition.")
    out = {"type": c["type"]}
    for key, allowed in spec["params"].items():
        v = c.get(key)
        if isinstance(allowed, list):
            if v not in allowed:
                raise ValueError(f"Choose a valid option in '{_plain(spec['label'])}'.")
            out[key] = v
        else:
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"Enter a number in '{_plain(spec['label'])}'.")
            lo, hi = allowed
            if not lo <= v <= hi:
                raise ValueError(f"In '{_plain(spec['label'])}', the {PARAM_NAMES.get(key, key)} must be between {lo} and {hi}.")
            out[key] = int(v) if key in ("n", "a", "b") else v
    return out


def validate(m: dict, taken: set[str]) -> dict:
    name = str(m.get("name", "")).strip()
    if not 2 <= len(name) <= 40:
        raise ValueError("Give the method a name of 2 to 40 characters.")
    if name in taken:
        raise ValueError(f"There is already a method called '{name}'.")
    if m.get("family") not in FAMILIES:
        raise ValueError("Choose what kind of method it is.")
    entry, exit_ = m.get("entry") or [], m.get("exit") or []
    if not 1 <= len(entry) <= MAX_CONDITIONS:
        raise ValueError(f"Add 1 to {MAX_CONDITIONS} 'buy when' conditions.")
    if len(exit_) > MAX_CONDITIONS:
        raise ValueError(f"Use at most {MAX_CONDITIONS} 'sell when' conditions.")
    return {"name": name, "family": m["family"],
            "entry": [_check(c) for c in entry], "exit": [_check(c) for c in exit_]}


def _series(c: dict, df: pd.DataFrame) -> pd.Series:
    t, close = c["type"], df.close
    above = c.get("op") in ("above", "more than")
    if t == "price_vs_ma":
        ma = ind.sma(close, c["n"])
        return (close > ma) if above else (close < ma)
    if t == "ma_vs_ma":
        a, b = ind.sma(close, c["a"]), ind.sma(close, c["b"])
        return (a > b) if above else (a < b)
    if t == "rsi":
        r = ind.rsi(close, c["n"])
        return (r > c["x"]) if above else (r < c["x"])
    if t == "new_high":
        return close > df.high.rolling(c["n"]).max().shift()
    if t == "new_low":
        return close < df.low.rolling(c["n"]).min().shift()
    if t == "macd":
        line, sig, _ = ind.macd(close)
        return (line > sig) if above else (line < sig)
    if t == "bollinger":
        lower, _, upper = ind.bollinger(close, 20, 2.0)
        band = upper if c["band"] == "upper" else lower
        return (close > band) if above else (close < band)
    if t == "volume":
        return df.volume > c["x"] * df.volume.rolling(c["n"]).mean()
    if t == "change":
        ch = (close / close.shift(c["n"]) - 1) * 100
        return (ch > c["x"]) if above else (ch < c["x"])
    if t == "adx":
        a = ind.adx(df)
        return (a > c["x"]) if above else (a < c["x"])
    raise ValueError(t)


def compile(m: dict) -> Strategy:
    entry, exit_ = m["entry"], m["exit"]

    def positions(df: pd.DataFrame) -> pd.Series:
        enter = pd.concat([_series(c, df) for c in entry], axis=1).all(axis=1)
        leave = pd.concat([_series(c, df) for c in exit_], axis=1).any(axis=1) if exit_ else ~enter
        return _state(enter_long=enter.fillna(False), exit_long=leave.fillna(False))

    low = lambda t: t[0].lower() + t[1:] if not t.startswith(("RSI", "MACD")) else t  # keep RSI/MACD capitals
    rule = "Buys when " + " and ".join(low(describe(c)) for c in entry) + ". "
    rule += ("Sells when " + " or ".join(low(describe(c)) for c in exit_) + "."
             if exit_ else "Sells as soon as that is no longer true.")
    return Strategy(m["name"], m["family"], "Your method. " + rule, positions)
