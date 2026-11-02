"""Report card: replay the agent's own rules over past years.

Walks forward one day at a time. On each day it uses only prices up to that day to
find fresh signals, score them (the same formula as live), size them (the same risk
rules), and open/close trades in a simulated account per market. Then it compares the
result with simply buying and holding the same assets.

Simplifications, stated in the report: trades fill at the day's closing price plus
slippage and fees; stops are checked on closing prices; the daily loss limit is not
applied (it needs intraday checks).
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import backtest, data, fx, methods, names
from . import indicators as ind
from .protect import new_stop, price_exit
from .analyzer import score_parts, total
from .broker import PaperBroker
from .paths import HOME
from .risk import RiskConfig, RiskManager
from .strategies import Strategy, positions

WARMUP = 504  # ~2 years of history before a symbol may be traded (long averages + a track record)
RESULT_FILE = HOME / "reports" / "report_card.json"


def _sharpe(mean: pd.Series, sd: pd.Series, ppy: float) -> np.ndarray:
    return np.where(sd.to_numpy() > 0, mean.to_numpy() / sd.replace(0, np.nan).to_numpy() * np.sqrt(ppy), 0.0)


@dataclass
class History:
    """Everything the agent would have known about one symbol on each past day."""
    symbol: str
    index: pd.DatetimeIndex
    close: np.ndarray
    atr: np.ndarray
    adx: np.ndarray
    bh_sharpe: np.ndarray
    target: dict[str, np.ndarray]
    age: dict[str, np.ndarray]
    sharpe: dict[str, np.ndarray]
    recent: dict[str, np.ndarray]
    max_dd: dict[str, np.ndarray]
    trades: dict[str, np.ndarray]
    strategies: list = None  # the methods replayed (the switched-on ones)


def prepare(symbol: str, market: str, mcfg: dict, cfg: dict, strategies: list[Strategy]) -> History:
    df = data.load(symbol, market, cfg["history_years"])
    ppy = len(df) / max((df.index[-1] - df.index[0]).days / 365.25, 1e-9)
    rets = df.close.pct_change().fillna(0)
    cost_rate = (mcfg["fee_bps"] + mcfg["slippage_bps"]) / 10_000
    n = cfg["recent_days"]
    h = History(symbol, df.index, df.close.to_numpy(), ind.atr(df).to_numpy(), ind.adx(df).fillna(0).to_numpy(),
                _sharpe(rets.expanding().mean(), rets.expanding().std().fillna(0), ppy), {}, {}, {}, {}, {}, {},
                strategies)
    for s in strategies:
        pos = positions(s, df, mcfg["allow_short"])
        held = pos.shift(1).fillna(0)
        strat = held * rets - held.diff().abs().fillna(held.abs()) * cost_rate
        equity = (1 + strat).cumprod()
        h.target[s.name] = pos.to_numpy().astype(int)
        h.age[s.name] = pos.groupby(pos.ne(pos.shift()).cumsum()).cumcount().to_numpy()
        # Expanding = only data up to that day, exactly like the live backtest on the history so far.
        h.sharpe[s.name] = _sharpe(strat.expanding().mean(), strat.expanding().std().fillna(0), ppy)
        h.recent[s.name] = _sharpe(strat.rolling(n, min_periods=20).mean(),
                                   strat.rolling(n, min_periods=20).std().fillna(0), ppy)
        h.max_dd[s.name] = (equity / equity.cummax() - 1).cummin().to_numpy()
        h.trades[s.name] = ((held != 0) & (held != held.shift())).cumsum().to_numpy()
    return h


def _score(h: History, i: int, s: Strategy, side: int, cfg: dict) -> float:
    others = [o for o in h.strategies if o is not s]
    agree = sum(h.target[o.name][i] == side for o in others)
    oppose = sum(h.target[o.name][i] == -side for o in others)
    return total(score_parts(h.sharpe[s.name][i], h.recent[s.name][i], h.bh_sharpe[i], agree, oppose,
                             len(others), s.family, h.adx[i], int(h.trades[s.name][i]),
                             h.max_dd[s.name][i], cfg["min_trades"]))


def candidates(h: History, i: int, cfg: dict) -> list[tuple[float, int, str]]:
    """(score, side, strategy) for the best fresh signal per direction on day i."""
    best: dict[int, tuple[float, int, str]] = {}
    for s in h.strategies:
        side = h.target[s.name][i]
        if side == 0 or h.age[s.name][i] >= cfg["fresh_signal_bars"]:
            continue
        sc = _score(h, i, s, side, cfg)
        if side not in best or sc > best[side][0]:
            best[side] = (sc, side, s.name)
    return list(best.values())


class _MemoryBroker(PaperBroker):
    """The paper broker's exact accounting, kept in memory (never touches your saved accounts)."""

    def __init__(self, market: str, cash: float, fee_bps: float, slippage_bps: float, margin: bool):
        self.market, self.margin = market, margin
        self.fee, self.slip = fee_bps / 10_000, slippage_bps / 10_000
        self.path = None
        self.state = {"cash": cash, "positions": {}, "trades": [], "day_start": None}

    def save(self):
        pass


def _fx(symbol: str, price: float, margin: bool) -> float:
    if not margin:
        return 1.0
    try:
        base, quote = fx.pair(symbol)
    except ValueError:
        return 1.0
    return 1.0 if quote == fx.ACCOUNT_CCY else (1.0 / price if base == fx.ACCOUNT_CCY else 1.0)


def simulate(market: str, cfg: dict, progress=None, strategies: list[Strategy] | None = None) -> dict:
    """Replay the agent in one market with the switched-on methods (or the given ones)."""
    mcfg = cfg["markets"][market]
    margin = bool(mcfg.get("margin"))
    rm = RiskManager(RiskConfig(**cfg["risk"]))
    strategies = strategies if strategies is not None else methods.active()
    hist: dict[str, History] = {}
    for sym in mcfg["symbols"]:
        if progress:
            progress(f"Preparing {names.symbol(sym)}")
        try:
            h = prepare(sym, market, mcfg, cfg, strategies)
        except Exception:
            continue
        if len(h.index) > WARMUP + 20:
            hist[sym] = h
    if not hist:
        return {"market": market, "name": names.market(market), "ok": False,
                "why": "Not enough price history (the replay needs more than 2 years)."}

    start = min(h.index[WARMUP] for h in hist.values())
    dates = sorted({d for h in hist.values() for d in h.index if d >= start})
    row = {s: {d: i for i, d in enumerate(h.index)} for s, h in hist.items()}
    cash0 = cfg["risk"]["starting_cash"]
    broker = _MemoryBroker(market, cash0, mcfg["fee_bps"], mcfg["slippage_bps"], margin)
    last_price: dict[str, float] = {}
    opened: dict[str, tuple] = {}
    trades, curve, invested = [], [], []
    reentry, stopped = cfg.get("reentry", {}), {}
    if progress:
        progress(f"Replaying {names.market(market)} day by day")

    for d in dates:
        today = {s: row[s][d] for s in hist if d in row[s]}
        for s, i in today.items():
            last_price[s] = hist[s].close[i]
        fxs = {s: _fx(s, p, margin) for s, p in last_price.items()}

        # 1. Exits: stop hit on the close, or the opening method no longer agrees.
        for s, pos in list(broker.positions.items()):
            if s not in today:
                continue
            h, i = hist[s], today[s]
            side = 1 if pos["qty"] > 0 else -1
            price = h.close[i]
            moved = new_stop(pos, price, cfg.get("protect", {}))  # profit protection, as live
            if moved:
                broker.update_stop(s, *moved)
                pos = broker.positions[s]
            hit = price_exit(pos, price)  # same check as live: safety net, then take-profit
            reason = None
            if hit:
                reason = "safety net hit" if hit.startswith("stop") else "take-profit reached"
            elif h.target[pos["strategy"]][i] != side:
                reason = "method changed its mind"
            if reason:
                t = broker.close(s, price, reason, fxs[s])
                d0, score = opened.pop(s)
                if reason == "safety net hit" and t["pnl"] < 0 and reentry.get("on"):
                    # Remember it: if the price recovers soon while the method still agrees, buy back in.
                    stopped[s] = {"day": i, "side": side, "strategy": pos["strategy"], "entry": pos["entry"],
                                  "exit": price}
                trades.append({"symbol": s, "name": names.symbol(s), "method": pos["strategy"],
                               "side": "Buy" if side > 0 else "Sell short", "opened": d0.date().isoformat(),
                               "closed": d.date().isoformat(), "days": (d - d0).days, "score": round(score),
                               "pnl": t["pnl"], "return_pct": t["pnl"] / t["cost"] * 100 if t["cost"] else 0,
                               "reason": reason})

        # 2. Entries: best-scoring fresh signals first, through the same risk checks as live.
        cands = []
        for s, i in today.items():
            if i >= WARMUP and s not in broker.positions:
                cands += [(sc, side, strat, s, i) for sc, side, strat in candidates(hist[s], i, cfg)]
        for s, so in list(stopped.items()):  # re-entry after being stopped out
            i = today.get(s)
            if i is None or s in broker.positions:
                continue
            if i - so["day"] > reentry.get("days", 5):
                del stopped[s]
                continue
            h, side = hist[s], so["side"]
            level = so["entry"] if reentry.get("trigger", "entry") == "entry" else so["exit"]
            strat = next(x for x in h.strategies if x.name == so["strategy"])
            if h.target[strat.name][i] == side and side * (h.close[i] - level) > 0:
                cands.append((_score(h, i, strat, side, cfg), side, strat.name, s, i))
                del stopped[s]
        for sc, side, strat, s, i in sorted(cands, reverse=True):
            if s in broker.positions:
                continue
            h, price = hist[s], hist[s].close[i]
            equity = broker.equity(last_price, fxs)
            stop = rm.stop_price(price, h.atr[i], side)
            qty = rm.size(equity, price, stop, fxs[s])
            qty = float(int(qty)) if market in ("us_stocks", "dse") else float(int(qty // 1000) * 1000) if margin else qty
            cost = qty * price if side > 0 and not margin else 0.0
            ok, _ = rm.check_new_trade(sc, len(broker.positions), equity, equity, broker.cash, cost, ignore_pause=True)
            if ok and qty > 0:
                broker.open(s, side, qty, price, stop, strat, sc, fxs[s], rm.target_price(price, stop, side, qty, fxs[s]))
                opened[s] = (d, sc)
        equity = broker.equity(last_price, fxs)
        curve.append((d, equity))
        invested.append(sum(abs(p["qty"]) * last_price[s] * fxs[s] for s, p in broker.positions.items()) / equity)

    eq = pd.Series([v for _, v in curve], index=[d for d, _ in curve])
    # "Doing nothing": put the same money into all the market's assets equally on day one and hold.
    closes = pd.DataFrame({s: pd.Series(h.close, index=h.index) for s, h in hist.items()}).reindex(eq.index).ffill()
    if margin:
        bench = pd.Series(cash0, index=eq.index)  # holding currencies isn't an investment; compare with cash
        bench_name = "Keeping the cash"
    else:
        bench = cash0 * (closes / closes.bfill().iloc[0]).mean(axis=1)
        bench_name = f"Buying all {len(hist)} assets and holding"
    summary = _summary(market, eq, bench, bench_name, trades, broker, len(hist))
    summary["avg_invested"] = float(np.mean(invested))
    return summary


def _stats(eq: pd.Series) -> dict:
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    rets = eq.pct_change().dropna()
    ppy = len(eq) / years
    return {"total_return": eq.iloc[-1] / eq.iloc[0] - 1,
            "per_year": (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1,
            "worst_drop": float((eq / eq.cummax() - 1).min()),
            "sharpe": float(rets.mean() / rets.std() * np.sqrt(ppy)) if rets.std() > 0 else 0.0}


def _summary(market, eq, bench, bench_name, trades, broker, n_assets) -> dict:
    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] <= 0]
    methods = {}
    for t in trades:
        m = methods.setdefault(t["method"], {"method": t["method"], "trades": 0, "wins": 0, "pnl": 0.0})
        m["trades"] += 1
        m["wins"] += t["pnl"] > 0
        m["pnl"] += t["pnl"]
    weekly = pd.DataFrame({"agent": eq, "bench": bench}).resample("W").last().dropna()
    return {
        "market": market, "name": names.market(market), "ok": True, "assets": n_assets,
        "currency": "BDT" if market == "dse" else "USD",
        "start": eq.index[0].date().isoformat(), "end": eq.index[-1].date().isoformat(),
        "years": round((eq.index[-1] - eq.index[0]).days / 365.25, 1),
        "agent": _stats(eq), "bench": _stats(bench), "bench_name": bench_name,
        "trades": len(trades), "win_rate": len(wins) / len(trades) if trades else 0,
        "avg_win": float(np.mean(wins)) if wins else 0, "avg_loss": float(np.mean(losses)) if losses else 0,
        "profit_factor": sum(wins) / -sum(losses) if losses and sum(losses) < 0 else None,
        "avg_days": float(np.mean([t["days"] for t in trades])) if trades else 0,
        "open_at_end": len(broker.positions),
        "methods": sorted(methods.values(), key=lambda m: m["pnl"], reverse=True),
        "recent_trades": trades[-15:][::-1],
        "chart": [{"d": d.date().isoformat(), "agent": round(r.agent, 2), "bench": round(r.bench, 2)}
                  for d, r in weekly.iterrows()],
    }


def run_all(cfg: dict, progress=None) -> dict:
    results = [simulate(m, cfg, progress) for m in cfg["markets"]]
    out = {"created": dt.datetime.now().strftime("%d %b %Y, %I:%M %p"), "min_score": cfg["risk"]["min_score"],
           "markets": results}
    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.write_text(json.dumps(out, default=float), encoding="utf-8")
    return out


METHOD_FILE = HOME / "reports" / "method_card.json"


def method_results(strategy: Strategy, cfg: dict) -> dict:
    """One method on its own: trade it on every asset of each market, after fees, over the full history.
    Simple per-method view; the full report card above shows the agent with all its rules."""
    out = {}
    for market, mcfg in cfg["markets"].items():
        rets, holds, sharpes, drops, trades, wins = [], [], [], [], 0, 0.0
        for sym in mcfg["symbols"]:
            try:
                df = data.load(sym, market, cfg["history_years"])
            except Exception:
                continue
            m, _ = backtest.run(df, positions(strategy, df, mcfg["allow_short"]), mcfg["fee_bps"], mcfg["slippage_bps"])
            rets.append(m.total_return)
            holds.append(m.buy_hold_return)
            sharpes.append(m.sharpe)
            drops.append(m.max_drawdown)
            trades += m.trades
            wins += m.win_rate * m.trades
        if rets:
            out[market] = {"avg_return": float(np.mean(rets)), "hold_return": float(np.mean(holds)),
                           "sharpe": float(np.median(sharpes)), "worst_drop": float(np.mean(drops)),
                           "trades": trades, "win_rate": wins / trades if trades else 0,
                           "beat_hold": int(sum(r > h for r, h in zip(rets, holds))), "assets": len(rets)}
    return out


def method_card(cfg: dict, progress=None) -> dict:
    card = {}
    for s in methods.available():
        if progress:
            progress(f"Testing {s.name}")
        card[s.name] = method_results(s, cfg)
    out = {"created": dt.datetime.now().strftime("%d %b %Y, %I:%M %p"), "methods": card}
    METHOD_FILE.parent.mkdir(parents=True, exist_ok=True)
    METHOD_FILE.write_text(json.dumps(out), encoding="utf-8")
    return out


def load_method_card() -> dict | None:
    return json.loads(METHOD_FILE.read_text(encoding="utf-8")) if METHOD_FILE.exists() else None


def load_saved() -> dict | None:
    return json.loads(RESULT_FILE.read_text(encoding="utf-8")) if RESULT_FILE.exists() else None
