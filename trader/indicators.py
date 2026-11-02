"""Technical indicators on pandas Series/DataFrames."""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    delta = s.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100)


def macd(s: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(s, fast) - ema(s, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def bollinger(s: pd.Series, n: int = 20, k: float = 2.0):
    mid = sma(s, n)
    sd = s.rolling(n).std()
    return mid - k * sd, mid, mid + k * sd


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["close"].shift()
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev).abs(),
        (df["low"] - prev).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def supertrend(df: pd.DataFrame, n: int = 10, mult: float = 3.0) -> pd.Series:
    """+1 while price is above the Supertrend line, -1 while below."""
    a = atr(df, n).to_numpy()
    mid = ((df["high"] + df["low"]) / 2).to_numpy()
    close = df["close"].to_numpy()
    upper, lower = mid + mult * a, mid - mult * a
    direction = np.ones(len(df))
    for i in range(1, len(df)):
        # Bands only tighten while the trend continues.
        if close[i - 1] <= upper[i - 1]:
            upper[i] = min(upper[i], upper[i - 1])
        if close[i - 1] >= lower[i - 1]:
            lower[i] = max(lower[i], lower[i - 1])
        if close[i] > upper[i - 1]:
            direction[i] = 1
        elif close[i] < lower[i - 1]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]
    out = pd.Series(direction, index=df.index)
    out.iloc[:n] = 0
    return out


def ichimoku(df: pd.DataFrame):
    """Conversion line, base line, and the cloud's top and bottom (shifted 26 days ahead)."""
    conv = (df["high"].rolling(9).max() + df["low"].rolling(9).min()) / 2
    base = (df["high"].rolling(26).max() + df["low"].rolling(26).min()) / 2
    span_a = ((conv + base) / 2).shift(26)
    span_b = ((df["high"].rolling(52).max() + df["low"].rolling(52).min()) / 2).shift(26)
    return conv, base, np.maximum(span_a, span_b), np.minimum(span_a, span_b)


def stochastic(df: pd.DataFrame, n: int = 14, d: int = 3):
    lo, hi = df["low"].rolling(n).min(), df["high"].rolling(n).max()
    k = 100 * (df["close"] - lo) / (hi - lo).replace(0, np.nan)
    return k, k.rolling(d).mean()


def keltner(df: pd.DataFrame, n: int = 20, mult: float = 1.5):
    mid = ema(df["close"], n)
    a = atr(df, n)
    return mid - mult * a, mid, mid + mult * a


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Trend strength: above ~25 trending, below ~20 ranging."""
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    a = atr(df, n)
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()
