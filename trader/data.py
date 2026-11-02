"""Historical daily price data, cached to disk.

US stocks, crypto and forex come from Yahoo Finance (yfinance).
Dhaka Stock Exchange data comes from the optional `bdshare` package.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from .paths import HOME

CACHE_DIR = HOME / "data_cache"
COLUMNS = ["open", "high", "low", "close", "volume"]

# When each market's trading day ends (local time zone, hour, minute).
DAY_ENDS = {
    "us_stocks": ("America/New_York", 16, 0),
    "dse": ("Asia/Dhaka", 14, 30),
    "crypto": ("UTC", 24, 0),            # Yahoo's crypto days run midnight to midnight UTC
    "forex": ("Europe/London", 22, 0),   # the FX day rolls over at 5 pm New York
}


def load(symbol: str, market: str, years: int = 6, refresh_hours: float = 4,
         finished_only: bool = True) -> pd.DataFrame:
    """Return daily OHLCV with a DatetimeIndex, oldest first.

    finished_only drops today's bar while the market is still trading. A half-finished
    day can flash a signal that is gone by the close; the backtests only ever saw full days."""
    CACHE_DIR.mkdir(exist_ok=True)
    path = CACHE_DIR / f"{market}_{symbol.replace('=', '_').replace('/', '_')}.csv"
    df = None
    if path.exists():
        age_h = (dt.datetime.now().timestamp() - path.stat().st_mtime) / 3600
        if age_h < refresh_hours:
            df = pd.read_csv(path, index_col=0, parse_dates=True)
    if df is None:
        df = _fetch_dse(symbol, years) if market == "dse" else _fetch_yahoo(symbol, years)
        df = df[COLUMNS].dropna(subset=["close"]).sort_index()
        df = df[~df.index.duplicated(keep="last")]
        if len(df) < 250:
            raise ValueError(f"{symbol}: only {len(df)} bars of history, need at least 250")
        df.to_csv(path)
    df = _clean(df)
    return _drop_unfinished(df, market) if finished_only else df


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Missing open/high/low prices (reported as 0, e.g. by DSE on quiet days) become the close."""
    bad = (df[["open", "high", "low"]] <= 0) | df[["open", "high", "low"]].isna()
    if bad.any().any():
        df = df.copy()
        for c in ("open", "high", "low"):
            df[c] = df[c].mask(bad[c], df["close"])
    return df


def _drop_unfinished(df: pd.DataFrame, market: str) -> pd.DataFrame:
    tz, hour, minute = DAY_ENDS.get(market, ("UTC", 24, 0))
    now = pd.Timestamp.now(tz=tz)
    still_trading = now.hour * 60 + now.minute < hour * 60 + minute
    if len(df) and df.index[-1].date() >= now.date() and still_trading:
        return df.iloc[:-1]
    return df


# Chart views: Yahoo period and bar size for each range, per market.
CHART_RANGES = {
    "minute": ("1d", "1m"),   # today's session, one candle per minute
    "day": ("1d", "5m"),
    "week": ("5d", "30m"),
    "month": ("1mo", "1d"),
    "year": ("1y", "1d"),
}


def chart(symbol: str, market: str, rng: str) -> pd.DataFrame:
    """Price bars for the Charts page (intraday for day/week)."""
    if market == "dse":
        if rng in ("minute", "day", "week"):
            raise ValueError("Minute-by-minute prices aren't available for Dhaka stocks. Try Month or Year.")
        df = load(symbol, market, finished_only=False)
        return df.iloc[-22:] if rng == "month" else df.iloc[-252:]
    if market == "us_stocks" and rng in ("minute", "day", "week"):
        try:
            return _alpaca_chart(symbol, rng)
        except Exception:
            pass  # no Alpaca keys or no connection: fall back to Yahoo
    import yfinance as yf

    period, interval = CHART_RANGES[rng]
    if market == "crypto" and rng == "week":
        period, interval = "7d", "1h"  # crypto trades all week
    raw = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)
    if rng in ("minute", "day") and raw.empty:  # weekend or holiday: show the last trading session
        raw = yf.Ticker(symbol).history(period="5d", interval=interval, auto_adjust=True)
        if not raw.empty:
            raw = raw[raw.index.date == raw.index[-1].date()]
    if raw.empty:
        raise ValueError(f"No chart data for {symbol} right now.")
    raw = raw.rename(columns=str.lower)
    raw.index = raw.index.tz_convert("Asia/Dhaka").tz_localize(None) if raw.index.tz else raw.index
    raw.attrs["source"] = "Yahoo Finance (can be a few minutes behind)"
    return raw[COLUMNS].dropna(subset=["close"])


def _alpaca_chart(symbol: str, rng: str) -> pd.DataFrame:
    """Live US stock prices from Alpaca, regular trading hours only (9:30-16:00 New York)."""
    from .live.alpaca import stock_bars

    days, timeframe = {"minute": (5, "1Min"), "day": (5, "5Min")}.get(rng, (9, "30Min"))
    start = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    bars = stock_bars(symbol, timeframe, start)
    if not bars:
        raise ValueError("no Alpaca bars")
    df = pd.DataFrame(bars)
    df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("t"), utc=True)).tz_convert("America/New_York")
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    minutes = df.index.hour * 60 + df.index.minute
    df = df[(minutes >= 9 * 60 + 30) & (minutes < 16 * 60)]
    if rng in ("minute", "day"):
        df = df[df.index.date == df.index[-1].date()]  # today's session, or the last one if closed
    else:
        df = df[df.index.normalize() >= df.index.normalize().unique()[-5:][0]]  # last 5 sessions
    df.index = df.index.tz_convert("Asia/Dhaka").tz_localize(None)
    df.attrs["source"] = "Alpaca (live)"
    return df[COLUMNS]


def _fetch_yahoo(symbol: str, years: int) -> pd.DataFrame:
    import yfinance as yf

    raw = yf.Ticker(symbol).history(period=f"{years}y", interval="1d", auto_adjust=True)
    if raw.empty:
        raise ValueError(f"{symbol}: no data returned from Yahoo Finance")
    raw.index = raw.index.tz_localize(None).normalize()
    return raw.rename(columns=str.lower)


def _fetch_dse(symbol: str, years: int) -> pd.DataFrame:
    try:
        from bdshare import get_hist_data
    except ImportError as e:
        raise ImportError("DSE data needs the `bdshare` package: pip install bdshare") from e

    end = dt.date.today()
    start = end - dt.timedelta(days=365 * years)
    raw = get_hist_data(start.isoformat(), end.isoformat(), symbol)
    if raw is None or len(raw) == 0:
        raise ValueError(f"{symbol}: no data returned from DSE")
    raw = raw.copy()
    raw.columns = [str(c).lower() for c in raw.columns]
    if "date" in raw.columns:
        raw = raw.set_index("date")
    raw.index = pd.to_datetime(raw.index)
    for c in COLUMNS:
        raw[c] = pd.to_numeric(raw[c].astype(str).str.replace(",", ""), errors="coerce")
    # DSE reports 0 for open/high/low on days a share did not trade.
    return raw[raw["close"] > 0]


# --- live price, for second-by-second profit/loss and the newest chart candle ---
_QUOTES: dict[tuple, tuple] = {}


def live_quote(symbol: str, market: str, max_age: float = 1.0) -> dict:
    """{"price", "source", "live"}: the freshest price available for free, cached for `max_age` seconds."""
    import time as _time

    key = (market, symbol)
    hit = _QUOTES.get(key)
    if hit and _time.time() - hit[0] < max_age:
        return hit[1]
    q = None
    if market == "us_stocks":
        try:
            from .live.alpaca import latest_trade
            q = {"price": latest_trade(symbol)[0], "source": "Alpaca (live)", "live": True}
        except Exception:
            q = None
    elif market == "crypto":
        q = _crypto_quote(symbol)
    if q is None and market != "dse":
        try:
            import yfinance as yf
            q = {"price": float(yf.Ticker(symbol).fast_info["last_price"]), "source": "Yahoo Finance", "live": True}
        except Exception:
            q = None
    if q is None:  # Dhaka, or every live source failed: the latest daily close
        q = {"price": float(load(symbol, market, finished_only=False).close.iloc[-1]),
             "source": "latest daily close", "live": False}
    _QUOTES[key] = (_time.time(), q)
    return q


def _crypto_quote(symbol: str) -> dict | None:
    import requests

    base = symbol.split("-")[0].upper()
    try:  # Binance public price (no account needed)
        r = requests.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": base + "USDT"}, timeout=4)
        if r.ok:
            return {"price": float(r.json()["price"]), "source": "Binance (live)", "live": True}
    except Exception:
        pass
    try:  # Coinbase public price
        r = requests.get(f"https://api.coinbase.com/v2/prices/{base}-USD/spot", timeout=4)
        if r.ok:
            return {"price": float(r.json()["data"]["amount"]), "source": "Coinbase (live)", "live": True}
    except Exception:
        pass
    return None
