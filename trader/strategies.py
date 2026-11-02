"""Rule-based strategy library.

Every strategy turns daily OHLCV into a target position per bar:
+1 long, 0 flat, -1 short. The position decided at a bar's close is held
from the next bar on (the backtester applies that shift).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from . import indicators as ind


@dataclass(frozen=True)
class Strategy:
    name: str
    family: str  # trend | mean_reversion | breakout | momentum
    rule: str    # plain-language description shown in reports
    positions: Callable[[pd.DataFrame], pd.Series]


def _state(enter_long, exit_long, enter_short=None, exit_short=None) -> pd.Series:
    """Build a held position from entry/exit events (events forward-filled)."""
    pos = pd.Series(np.nan, index=enter_long.index)
    pos[exit_long] = 0
    if enter_short is not None:
        pos[exit_short] = 0
        pos[enter_short] = -1
    pos[enter_long] = 1
    return pos.ffill().fillna(0)


def _sma_cross(df):
    fast, slow = ind.sma(df.close, 50), ind.sma(df.close, 200)
    return np.sign(fast - slow).fillna(0)


def _ema_cross(df):
    fast, slow = ind.ema(df.close, 12), ind.ema(df.close, 26)
    return np.sign(fast - slow).where(df.index >= df.index[26], 0)


def _macd(df):
    line, sig, _ = ind.macd(df.close)
    trend = np.sign(df.close - ind.sma(df.close, 100))
    # Only take MACD crosses in the direction of the 100-day trend.
    raw = np.sign(line - sig)
    return raw.where(raw == trend, 0).fillna(0)


def _rsi_reversion(df):
    r = ind.rsi(df.close, 14)
    above_trend = df.close > ind.sma(df.close, 200)
    return _state(
        enter_long=(r < 30) & above_trend, exit_long=r > 55,
        enter_short=(r > 70) & ~above_trend, exit_short=r < 45,
    )


def _bollinger_reversion(df):
    lower, mid, upper = ind.bollinger(df.close, 20, 2.0)
    return _state(
        enter_long=df.close < lower, exit_long=df.close > mid,
        enter_short=df.close > upper, exit_short=df.close < mid,
    )


def _donchian_breakout(df):
    hi = df.high.rolling(20).max().shift()
    lo = df.low.rolling(20).min().shift()
    exit_hi = df.high.rolling(10).max().shift()
    exit_lo = df.low.rolling(10).min().shift()
    return _state(
        enter_long=df.close > hi, exit_long=df.close < exit_lo,
        enter_short=df.close < lo, exit_short=df.close > exit_hi,
    )


def _momentum(df):
    # 6-month return, skipping the latest month; re-decided every ~21 bars.
    mom = df.close.shift(21) / df.close.shift(126) - 1
    pos = np.sign(mom)
    rebalance = pd.Series(np.arange(len(df)) % 21 == 0, index=df.index)
    return pos.where(rebalance).ffill().fillna(0)


def _supertrend(df):
    return ind.supertrend(df, 10, 3.0)


def _ichimoku(df):
    conv, base, cloud_top, cloud_bottom = ind.ichimoku(df)
    up = (df.close > cloud_top) & (conv > base)
    down = (df.close < cloud_bottom) & (conv < base)
    return pd.Series(np.where(up, 1, np.where(down, -1, 0)), index=df.index).astype(float)


def _triple_ma(df):
    a, b, c = ind.ema(df.close, 10), ind.ema(df.close, 20), ind.ema(df.close, 50)
    pos = np.where((a > b) & (b > c), 1, np.where((a < b) & (b < c), -1, 0))
    return pd.Series(pos, index=df.index).where(df.index >= df.index[50], 0).astype(float)


def _volume_breakout(df):
    # Forex has no volume data on Yahoo, so this method stays quiet there.
    hi = df.high.rolling(20).max().shift()
    loud = df.volume > 1.5 * df.volume.rolling(20).mean()
    return _state(enter_long=(df.close > hi) & loud, exit_long=df.close < ind.sma(df.close, 20))


def _stochastic_dip(df):
    k, d = ind.stochastic(df, 14, 3)
    uptrend = df.close > ind.sma(df.close, 200)
    turn_up = (k > d) & (k.shift() <= d.shift()) & (k < 25)
    return _state(enter_long=turn_up & uptrend, exit_long=k > 80)


def _squeeze(df):
    lower_bb, _, upper_bb = ind.bollinger(df.close, 20, 2.0)
    lower_kc, mid, upper_kc = ind.keltner(df, 20, 1.5)
    squeezed = ((upper_bb < upper_kc) & (lower_bb > lower_kc)).rolling(5).max().astype(bool)
    return _state(
        enter_long=squeezed & (df.close > upper_kc), exit_long=df.close < mid,
        enter_short=squeezed & (df.close < lower_kc), exit_short=df.close > mid,
    )


# Where shorting is allowed (forex), each rule also runs in reverse to sell short.
# --- From the strike.money "70 trading strategies" article, turned into exact daily rules ---

def _range_bounce(df):
    """Range trading: buy near the bottom of an established sideways range, sell near the top."""
    hi, lo = df.high.rolling(40).max().shift(), df.low.rolling(40).min().shift()
    width = (hi - lo) / lo
    sideways = (ind.adx(df) < 20) & (width < 0.15)
    where = (df.close - lo) / (hi - lo)
    return _state(
        enter_long=sideways & (where < 0.15) & (df.close > df.open), exit_long=(where > 0.85) | (df.close < lo),
        enter_short=sideways & (where > 0.85) & (df.close < df.open), exit_short=(where < 0.15) | (df.close > hi),
    )


def _hammer(df):
    """Price action: a hammer candle after a pullback, in an uptrend."""
    body = (df.close - df.open).abs()
    rng = (df.high - df.low).replace(0, np.nan)
    lower_wick = df[["open", "close"]].min(axis=1) - df.low
    hammer = (lower_wick >= 2 * body) & ((df.close - df.low) / rng >= 0.6)
    sma50 = ind.sma(df.close, 50)
    uptrend = (df.close > sma50) & (sma50 > sma50.shift(10))
    pulled_back = df.close < 0.97 * df.high.rolling(10).max()
    return _state(enter_long=hammer & uptrend & pulled_back,
                  exit_long=(df.close < sma50) | (ind.rsi(df.close) > 70))


def _fibonacci(df, n=60):
    """Fibonacci pullback: after a 60-day up-swing, buy a bullish close in the 50-61.8% 'golden zone'."""
    hi, lo = df.high.rolling(n).max(), df.low.rolling(n).min()
    swing_up = df.high.rolling(n).apply(np.argmax, raw=True) > df.low.rolling(n).apply(np.argmin, raw=True)
    retrace = (hi - df.close) / (hi - lo).replace(0, np.nan)
    big_swing = (hi - lo) / lo > 0.08
    enter = swing_up & big_swing & retrace.between(0.45, 0.65) & (df.close > df.open) & \
        (df.close > ind.sma(df.close, 200))
    return _state(enter_long=enter, exit_long=(df.close >= hi.shift()) | (retrace > 0.786))


def _seasonal(df):
    """Seasonal: hold only in calendar months that were up on average in earlier years (never this year)."""
    month = df.index.to_period("M")
    monthly = df.close.groupby(month).last().pct_change()
    past_avg = monthly.groupby(monthly.index.month).transform(lambda x: x.shift(1).expanding(min_periods=3).mean())
    good = (past_avg > 0.005).astype(float)
    return pd.Series(month.map(good), index=df.index).fillna(0).astype(float)


def _hold_until(enter: pd.Series, leave: pd.Series, max_days: int) -> pd.Series:
    """Long from an entry until an exit signal or max_days, whichever comes first."""
    pos, held = np.zeros(len(enter)), 0
    e, x = enter.to_numpy(), leave.to_numpy()
    for i in range(len(e)):
        if held:
            held += 1
            if x[i] or held > max_days:
                held = 0
        elif e[i]:
            held = 1
        pos[i] = 1 if held else 0
    return pd.Series(pos, index=enter.index)


def _gap_fill(df):
    """Gap trading: after a 2%+ gap down where buyers step in (close above open), bet on the gap filling."""
    prev = df.close.shift()
    gap_down = (df.open / prev - 1 < -0.02) & (df.close > df.open)
    gap_level = prev.where(gap_down).ffill()
    return _hold_until(gap_down, df.close >= gap_level, 5)


def _rsi2(df):
    """Mean reversion: RSI(2) below 10 in a long-term uptrend; sell once the price is back above its 5-day average."""
    return _state(enter_long=(ind.rsi(df.close, 2) < 10) & (df.close > ind.sma(df.close, 200)),
                  exit_long=df.close > ind.sma(df.close, 5))


def _divergence(df):
    """Reversal: price makes a new 20-day low but RSI doesn't (bullish divergence), then MACD turns up."""
    r = ind.rsi(df.close)
    new_low = df.close < df.close.rolling(20).min().shift()
    diverging = new_low & (r > r.rolling(20).min().shift() + 5)
    recent = diverging.rolling(5).max().fillna(0).astype(bool)
    line, sig, _ = ind.macd(df.close)
    turn_up = (line > sig) & (line.shift() <= sig.shift())
    return _state(enter_long=recent & turn_up,
                  exit_long=(line < sig) | (df.close < df.close.rolling(20).min().shift()))


STRATEGIES: list[Strategy] = [
    Strategy("Long-term trend", "trend",
             "Buys when the 50-day average price rises above the 200-day average, and sells when it "
             "drops below. Slow, catches big moves.", _sma_cross),
    Strategy("Short-term trend", "trend",
             "Buys when the 12-day average price rises above the 26-day average, and sells when it "
             "drops below. Reacts faster than the long-term trend.", _ema_cross),
    Strategy("Momentum shift", "trend",
             "Buys when short-term momentum turns upward (the MACD indicator), but only while the price "
             "is above its 100-day average.", _macd),
    Strategy("Buy the dip", "mean_reversion",
             "Buys after a sharp drop (RSI below 30) while the long-term trend is still up, and sells "
             "once the price recovers.", _rsi_reversion),
    Strategy("Snap-back", "mean_reversion",
             "Buys when the price falls unusually far below its 20-day average (Bollinger bands), and "
             "sells when it returns to that average.", _bollinger_reversion),
    Strategy("Breakout", "breakout",
             "Buys when the price reaches a new 20-day high, and sells when it falls to a 10-day low.",
             _donchian_breakout),
    Strategy("6-month momentum", "momentum",
             "Holds assets that rose over the past 6 months (ignoring the latest month). Re-checked "
             "monthly.", _momentum),
    Strategy("Supertrend", "trend",
             "Follows a trailing line set 3 times the daily price range away from the price. Buys when "
             "the price closes above it, and sells when it closes below.", _supertrend),
    Strategy("Ichimoku cloud", "trend",
             "Buys when the price is above the Ichimoku 'cloud' and the short-term line is above the "
             "medium-term line; steps aside inside the cloud.", _ichimoku),
    Strategy("Triple average", "trend",
             "Buys only when the 10-, 20- and 50-day averages are lined up upward (10 above 20 above "
             "50), and sells when that order breaks.", _triple_ma),
    Strategy("Volume breakout", "breakout",
             "Buys a new 20-day high only when trading volume is at least 1.5 times normal, and "
             "sells when the price drops below its 20-day average.", _volume_breakout),
    Strategy("Stochastic dip", "mean_reversion",
             "In an uptrend (above the 200-day average), buys when the Stochastic indicator turns up "
             "from deep oversold (below 25), and sells when it reaches overbought (above 80).",
             _stochastic_dip),
    Strategy("Squeeze breakout", "breakout",
             "Waits for a quiet period (Bollinger bands inside Keltner channels), then buys when the "
             "price breaks out upward, and sells back at the 20-day average.", _squeeze),
    Strategy("Range bounce", "mean_reversion",
             "When a market moves sideways in a tight 40-day range, buys near the bottom of the range on an "
             "up day and sells near the top, or if the range breaks. (strike.money article)", _range_bounce),
    Strategy("Hammer in uptrend", "trend",
             "In an uptrend, after a 3% pullback, buys a 'hammer' candle (long lower wick, close near the high). "
             "Sells below the 50-day average or when RSI is above 70. (strike.money article)", _hammer),
    Strategy("Fibonacci pullback", "trend",
             "After a 60-day up-swing of at least 8%, buys an up day in the 50-61.8% Fibonacci 'golden zone'. "
             "Sells at a new high, or if the pullback goes past 78.6%. (strike.money article)", _fibonacci),
    Strategy("Seasonal months", "momentum",
             "Holds the asset only in calendar months that rose on average in earlier years (needs 3+ years "
             "of history; never uses the current year). (strike.money article)", _seasonal),
    Strategy("Gap fill", "mean_reversion",
             "After the price opens 2%+ below yesterday's close and then closes above its open, buys and holds "
             "until the gap is filled, at most 5 days. (strike.money article)", _gap_fill),
    Strategy("Deep dip (RSI-2)", "mean_reversion",
             "In a long-term uptrend, buys when the 2-day RSI drops below 10, and sells once the price closes "
             "above its 5-day average. (strike.money article)", _rsi2),
    Strategy("RSI divergence", "mean_reversion",
             "Buys when the price makes a new 20-day low but RSI does not (a sign selling is fading) and then "
             "MACD turns up. Sells when MACD turns down again. (strike.money article)", _divergence),
]
BUILT_IN = {s.name for s in STRATEGIES}


def positions(strategy: Strategy, df: pd.DataFrame, allow_short: bool) -> pd.Series:
    pos = strategy.positions(df).astype(float)
    return pos if allow_short else pos.clip(lower=0)
