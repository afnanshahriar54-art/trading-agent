"""Vectorised daily backtester with fees and slippage."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class Metrics:
    total_return: float
    cagr: float
    sharpe: float
    max_drawdown: float  # negative fraction, e.g. -0.25
    trades: int
    win_rate: float
    exposure: float      # fraction of days in the market
    buy_hold_return: float

    def short(self) -> str:
        return (f"return {self.total_return:+.0%}, CAGR {self.cagr:+.1%}, Sharpe {self.sharpe:.2f}, "
                f"max DD {self.max_drawdown:.0%}, {self.trades} trades, win {self.win_rate:.0%}")


def run(df: pd.DataFrame, pos: pd.Series, fee_bps: float, slippage_bps: float,
        carry_pct_year: float = 0.0) -> tuple[Metrics, pd.Series]:
    """Return metrics and the equity curve (starting at 1.0). `carry_pct_year`: overnight financing
    charged on the position's size for every day it's held (forex swap), as % per year."""
    # Bars per year from the data itself: ~252 for stocks, ~260 for forex, 365 for crypto.
    span_years = max((df.index[-1] - df.index[0]).days / 365.25, 1e-9)
    periods_per_year = len(df) / span_years
    held = pos.shift(1).fillna(0)                 # decide at close, hold from next bar
    rets = df["close"].pct_change().fillna(0)
    turnover = held.diff().abs().fillna(held.abs())
    cost = turnover * (fee_bps + slippage_bps) / 10_000 + held.abs() * carry_pct_year / 100 / periods_per_year
    strat = held * rets - cost
    equity = (1 + strat).cumprod()

    years = span_years
    total = equity.iloc[-1] - 1
    cagr = equity.iloc[-1] ** (1 / years) - 1 if equity.iloc[-1] > 0 else -1.0
    sd = strat.std()
    sharpe = float(strat.mean() / sd * np.sqrt(periods_per_year)) if sd > 0 else 0.0
    max_dd = float((equity / equity.cummax() - 1).min())

    trade_returns = _trade_returns(held, strat)
    wins = sum(r > 0 for r in trade_returns)
    return Metrics(
        total_return=float(total),
        cagr=float(cagr),
        sharpe=sharpe,
        max_drawdown=max_dd,
        trades=len(trade_returns),
        win_rate=wins / len(trade_returns) if trade_returns else 0.0,
        exposure=float((held != 0).mean()),
        buy_hold_return=float(df["close"].iloc[-1] / df["close"].iloc[0] - 1),
    ), equity


def _trade_returns(held: pd.Series, strat: pd.Series) -> list[float]:
    """Compound daily strategy returns over each continuous holding period."""
    trade_id = (held != held.shift()).cumsum()
    out = []
    for _, grp in strat[held != 0].groupby(trade_id[held != 0]):
        out.append(float((1 + grp).prod() - 1))
    return out
