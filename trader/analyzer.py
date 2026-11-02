"""Analysis: run every strategy on every symbol, find fresh signals, score them
and explain the reasoning in plain language."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import backtest, data, fx, methods, names
from . import indicators as ind
from .strategies import Strategy, positions

TREND_FAMILIES = {"trend", "breakout", "momentum"}


@dataclass
class StrategyView:
    strategy: Strategy
    target: int          # current target position
    age: int             # bars since the target last changed
    full: backtest.Metrics
    recent: backtest.Metrics


@dataclass
class SymbolAnalysis:
    market: str
    symbol: str
    price: float
    atr: float
    adx: float
    bh_sharpe: float
    views: list[StrategyView]
    fx: float = 1.0      # multiply quote-currency amounts by this to get account currency
    years: float = 0.0   # length of the price history used
    bh_return: float = 0.0  # return from simply buying and holding over that history
    # Switched-on methods: only these produce ideas and count as agreeing/disagreeing.
    # Switched-off ones are still analysed, so trades they opened earlier can still be managed.
    active: frozenset = frozenset()

    def active_views(self) -> list[StrategyView]:
        return [v for v in self.views if v.strategy.name in self.active]

    def target_of(self, strategy_name: str) -> int | None:
        return next((v.target for v in self.views if v.strategy.name == strategy_name), None)


@dataclass
class Candidate:
    market: str
    symbol: str
    side: int
    view: StrategyView
    analysis: SymbolAnalysis
    score: float = 0.0
    parts: dict[str, float] = field(default_factory=dict)
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)
    supporters: list[str] = field(default_factory=list)

    @property
    def side_word(self) -> str:
        return "BUY" if self.side > 0 else "SELL SHORT"


SCORE_GROUPS = [("Under 40", 0, 40), ("40 to 54", 40, 55), ("55 to 69", 55, 70), ("70 and up", 70, 1000)]


def by_score(rows: list[dict], result: str) -> list[dict]:
    """Results grouped by confidence score: how many, how often they made money, average result.
    The score is a ranking, not a chance of winning; this is how to check what it's worth."""
    out = []
    for label, lo, hi in SCORE_GROUPS:
        xs = [r[result] for r in rows if lo <= r["score"] < hi]
        if xs:
            out.append({"group": label, "count": len(xs), "win_rate": sum(x > 0 for x in xs) / len(xs),
                        "avg": sum(xs) / len(xs)})
    return out


def analyze_symbol(market: str, symbol: str, mcfg: dict, cfg: dict,
                   strategies: list[Strategy] | None = None, active: set[str] | None = None) -> SymbolAnalysis:
    """`strategies`: every method to analyse (default: all available); `active`: the switched-on ones."""
    df = data.load(symbol, market, cfg["history_years"])
    recent_n = cfg["recent_days"]
    strategies = strategies if strategies is not None else methods.available()
    active = active if active is not None else methods.active_names()
    views = []
    for strat in strategies:
        pos = positions(strat, df, mcfg["allow_short"])
        carry = mcfg.get("carry_pct_year", 0)
        full, _ = backtest.run(df, pos, mcfg["fee_bps"], mcfg["slippage_bps"], carry)
        recent, _ = backtest.run(df.iloc[-recent_n:], pos.iloc[-recent_n:], mcfg["fee_bps"], mcfg["slippage_bps"], carry)
        changed = pos.ne(pos.shift()).to_numpy().nonzero()[0]
        age = len(pos) - 1 - (changed[-1] if len(changed) else 0)
        views.append(StrategyView(strat, int(pos.iloc[-1]), int(age), full, recent))

    bh, _ = backtest.run(df, pd.Series(1.0, index=df.index), 0, 0)
    price = float(df.close.iloc[-1])
    return SymbolAnalysis(
        market=market, symbol=symbol, price=price,
        atr=float(ind.atr(df).iloc[-1]), adx=float(ind.adx(df).iloc[-1]),
        bh_sharpe=bh.sharpe, views=views,
        fx=fx.quote_to_usd(symbol, price) if mcfg.get("margin") else 1.0,
        years=(df.index[-1] - df.index[0]).days / 365.25, bh_return=bh.total_return,
        active=frozenset(active),
    )


def candidates(a: SymbolAnalysis, cfg: dict) -> list[Candidate]:
    """One candidate per symbol+direction: the best-scoring strategy with a fresh signal,
    with the other agreeing strategies listed as support."""
    fresh = [v for v in a.active_views() if v.target != 0 and v.age < cfg["fresh_signal_bars"]]
    best: dict[int, Candidate] = {}
    for v in fresh:
        c = score(Candidate(a.market, a.symbol, v.target, v, a), cfg)
        if v.target not in best or c.score > best[v.target].score:
            best[v.target] = c
    return list(best.values())


def _pct(x: float) -> str:
    """+12% / -3% / 0% (never '-0%')."""
    return "0%" if abs(x) < 0.005 else f"{x:+.0%}"


def score_parts(sharpe: float, recent_sharpe: float, bh_sharpe: float, agree: int, oppose: int,
                others: int, family: str, adx: float, trades: int, max_drawdown: float,
                min_trades: int) -> dict[str, float]:
    """The confidence score's ingredients. Shared by the live analysis and the historical replay,
    so the replay judges past trades exactly as the agent judges today's."""
    parts = {
        "base": 20.0,
        # 1. Historical quality on this exact symbol (0..30)
        "history": float(np.interp(sharpe, [-0.5, 1.5], [0, 30])),
        # 2. Still working recently? (-10..10)
        "recent": float(np.interp(recent_sharpe, [-1, 0, 1.5], [-10, 0, 10])),
        # 3. Better than simply holding? (0..10)
        "edge": float(np.interp(sharpe - bh_sharpe, [-0.5, 0.5], [0, 10])),
        # 4. Confluence: other strategies pointing the same way (-10..20)
        "confluence": 20 * (agree - 0.5 * oppose) / max(others, 1),
    }
    # 5. Market regime fit (-5..10)
    if family in TREND_FAMILIES:
        parts["regime"] = 10.0 if adx > 25 else (-5.0 if adx < 20 else 2.0)
    else:
        parts["regime"] = 10.0 if adx < 20 else (-5.0 if adx > 30 else 2.0)
    # 6. Robustness penalties
    if trades < min_trades:
        parts["few_trades"] = -15.0
    if max_drawdown < -0.40:
        parts["drawdown"] = -10.0
    return parts


def total(parts: dict[str, float]) -> float:
    return float(np.clip(sum(parts.values()), 0, 100))


def score(c: Candidate, cfg: dict) -> Candidate:
    """Score a candidate and explain each ingredient in plain language."""
    v, a = c.view, c.analysis
    m, r = v.full, v.recent
    others = [x for x in a.active_views() if x is not v]
    agree = [x.strategy.name for x in others if x.target == c.side]
    oppose = [x.strategy.name for x in others if x.target == -c.side]
    c.supporters = agree
    parts = score_parts(m.sharpe, r.sharpe, a.bh_sharpe, len(agree), len(oppose), len(others),
                        v.strategy.family, a.adx, m.trades, m.max_drawdown, cfg["min_trades"])

    name = names.symbol(a.symbol)
    yrs = f"{a.years:.0f} years"

    record = (f"over the past {yrs} this method made {_pct(m.total_return)} on {name}, "
              f"and {m.win_rate:.0%} of its {m.trades} trades were winners")
    if m.sharpe >= 0.8:
        c.pros.append(f"Strong track record: {record}.")
    elif m.sharpe <= 0.2:
        c.cons.append(f"Weak track record: {record}.")
    else:
        c.pros.append(f"Decent track record: {record}.")

    if r.sharpe > 0.3:
        c.pros.append(f"It is still working: {_pct(r.total_return)} in the last 12 months.")
    else:
        c.cons.append(f"It has struggled lately: {_pct(r.total_return)} in the last 12 months.")

    hold = f"simply buying and holding {name} made {_pct(a.bh_return)} over the same {yrs}"
    if m.sharpe > a.bh_sharpe:
        c.pros.append(f"Better reward for the risk than doing nothing ({hold}).")
    else:
        c.cons.append(f"Doing nothing gave a better reward for the risk ({hold}).")

    if agree:
        c.pros.append(f"{len(agree)} of the {len(others)} other methods agree ({', '.join(agree)}).")
    if oppose:
        c.cons.append(f"{len(oppose)} other methods say the opposite ({', '.join(oppose)}).")

    trending = a.adx > 25
    ranging = a.adx < 20
    mood = ("moving in a clear trend" if trending else "moving sideways without a clear trend" if ranging
            else "neither clearly trending nor clearly sideways")
    kind = "trend-following" if v.strategy.family in TREND_FAMILIES else "buy-the-dip"
    if parts["regime"] > 0:
        c.pros.append(f"{name} is {mood} right now, which suits a {kind} method.")
    else:
        c.cons.append(f"{name} is {mood} right now, which does not suit a {kind} method.")

    if "few_trades" in parts:
        c.cons.append(f"Only {m.trades} past trades, which is too few to trust the track record.")
    if "drawdown" in parts:
        c.cons.append(f"Bumpy: at its worst this method was down {-m.max_drawdown:.0%} from its peak.")

    c.parts = parts
    c.score = total(parts)
    return c


PART_LABELS = {
    "history": "track record", "recent": "recent results",
    "edge": "reward for the risk", "confluence": "agreement between methods",
    "regime": "fit with today's market", "few_trades": "amount of history",
    "drawdown": "smoothness",
}


def compare(winner: Candidate, runner_up: Candidate) -> str:
    """Explain in one paragraph why the winner ranks above the runner-up."""
    diffs = []
    for k, label in PART_LABELS.items():
        d = winner.parts.get(k, 0) - runner_up.parts.get(k, 0)
        if abs(d) >= 2:
            diffs.append((d, label))
    diffs.sort(reverse=True)
    ahead = [label for d, label in diffs if d > 0][:3]
    behind = [label for d, label in sorted(diffs) if d < 0][:2]
    w, r = names.symbol(winner.symbol), names.symbol(runner_up.symbol)
    text = f"{w} ({winner.score:.0f}/100) ranks above {r} ({runner_up.score:.0f}/100)"
    text += f" mainly because of its {', '.join(ahead)}." if ahead else " by a narrow margin."
    if behind:
        text += f" {r} is better on {', '.join(behind)}, but not by enough."
    return text
