"""The agent loop: analyse -> manage open positions -> rank new ideas -> risk check -> trade."""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import analyzer, data, methods, settings, track
from .broker import PaperBroker
from .live import BROKERS, BrokerError
from .paths import HOME
from .protect import manage as protect_positions, price_exit
from .risk import RiskConfig, RiskManager

ROOT = HOME
MANUAL = "Your trade"  # the "strategy" recorded for trades you place yourself from a chart
REPORT_DIR = ROOT / "reports"


def load_config(path: Path = ROOT / "config.yaml") -> dict:
    """config.yaml, with the safety rules you changed on the Settings page applied on top."""
    cfg = yaml.safe_load(path.read_text())
    s = settings.load()
    cfg["risk"].update(s["rules"])
    cfg["risk"]["take_profit"] = s["take_profit"]
    cfg["protect"] = s["protect"]
    return cfg


def scan(cfg: dict, markets: list[str] | None = None, verbose: bool = True, progress=None):
    """Analyse every symbol. Returns ({market: [SymbolAnalysis]}, [Candidate] ranked).
    `progress(done, total, market, symbol)` is called before each symbol, if given."""
    analyses: dict[str, list[analyzer.SymbolAnalysis]] = {}
    cands: list[analyzer.Candidate] = []
    todo = [(m, s) for m, mcfg in cfg["markets"].items() if not markets or m in markets for s in mcfg["symbols"]]
    # Download prices for all symbols at once (several at a time); the analysis below then reads the cache.
    if progress:
        progress(0, len(todo), "all", "Downloading prices")
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda ms: _prefetch(ms[1], ms[0], cfg["history_years"]), todo))
    strategies, active = methods.available(), methods.active_names()  # read settings once per scan
    for i, (market, symbol) in enumerate(todo):
        if progress:
            progress(i, len(todo), market, symbol)
        analyses.setdefault(market, [])
        try:
            a = analyzer.analyze_symbol(market, symbol, cfg["markets"][market], cfg, strategies, active)
        except Exception as e:  # one bad symbol must not stop the scan
            if verbose:
                print(f"  ! {market}/{symbol}: {e}")
            continue
        analyses[market].append(a)
        cands.extend(analyzer.candidates(a, cfg))
    cands.sort(key=lambda c: c.score, reverse=True)
    try:
        track.record(cands, cfg)
    except Exception as e:  # the track record must never stop a scan
        if verbose:
            print(f"  ! track record: {e}")
    return analyses, cands


def _prefetch(symbol: str, market: str, years: int) -> None:
    try:
        data.load(symbol, market, years)
    except Exception:
        pass  # reported when the symbol is analysed


@dataclass
class Proposal:
    """A sized trade idea with the risk manager's verdict."""
    candidate: analyzer.Candidate
    qty: float
    stop: float
    risk_amount: float   # account currency lost if the stop is hit
    notional: float      # position size in account currency
    cost: float          # cash spent on entry (0 for margin accounts)
    ok: bool
    why: str             # "ok" or the reason the risk manager blocks it
    target: float | None = None  # take-profit price (None when switched off)

    @property
    def summary(self) -> str:
        c, a = self.candidate, self.candidate.analysis
        return (f"{c.side_word} {self.qty:g} {c.symbol} @ ~{a.price:.4g}, stop {self.stop:.4g} "
                f"(notional ~{self.notional:,.0f}, risk ~{self.risk_amount:,.0f}; "
                f"score {c.score:.0f}, {c.view.strategy.name})")


def propose(c: analyzer.Candidate, broker, risk: RiskManager, prices: dict, fxs: dict) -> Proposal:
    """Size a candidate against the broker's current state and run the risk checks."""
    a, market = c.analysis, c.market
    equity = broker.equity(prices, fxs)
    day_start = broker.day_start_equity(prices, fxs)
    stop = risk.stop_price(a.price, a.atr, c.side)
    qty = risk.size(equity, a.price, stop, a.fx)
    if market in ("us_stocks", "dse"):
        qty = float(int(qty))  # whole shares
    elif broker.margin:
        qty = float(int(qty // 1000) * 1000)  # forex trades in 1,000-unit micro lots (round down)
    try:
        qty = broker.round_qty(c.symbol, qty)  # exchange lot sizes (Binance)
    except BrokerError as e:
        return Proposal(c, 0, stop, 0, 0, 0, False, str(e))
    # Margin accounts spend no cash on entry; the position cap limits their size instead.
    cost = qty * a.price if c.side > 0 and not broker.margin else 0.0
    ok, why = risk.check_new_trade(c.score, len(broker.positions), equity, day_start, broker.cash, cost)
    if ok and qty <= 0:
        ok, why = False, "position size rounds to zero"
    return Proposal(c, qty, stop, qty * abs(a.price - stop) * a.fx, qty * a.price * a.fx, cost, ok, why,
                    risk.target_price(a.price, stop, c.side, qty, a.fx))


def exit_reason(pos: dict, a: analyzer.SymbolAnalysis) -> str | None:
    """Why an agent-managed position should be closed now, or None to keep it."""
    reason = price_exit(pos, a.price)
    if reason:
        return reason
    side = 1 if pos["qty"] > 0 else -1
    signal = a.target_of(pos["strategy"])
    if signal is None:  # your own trade (MANUAL): only its safety net or take-profit closes it
        return None
    if signal != side:
        return f"{pos['strategy']} no longer signals this direction"
    return None


def run(cfg: dict, mode: str, markets: list[str] | None = None, ask=input,
        methods: list[str] | None = None) -> Path:
    """One agent cycle. `methods`, if given, limits new trades to those methods (auto-mode rules)."""
    if cfg.get("live_trading"):
        raise SystemExit("Real-money trading is not available yet. Set live_trading: false.")
    risk = RiskManager(RiskConfig(**cfg["risk"]))
    analyses, cands = scan(cfg, markets)
    lines = _header(cfg, mode, analyses, cands)

    for market, mlist in analyses.items():
        mcfg = cfg["markets"][market]
        lines.append(f"\n## {market}\n")
        try:
            broker = make_broker(market, cfg)
            broker.positions  # connect now so a bad key fails here, not mid-trade
        except BrokerError as e:
            lines.append(f"- BROKER UNAVAILABLE ({mcfg.get('broker', 'paper')}): {e}. Skipping {market}.")
            continue
        lines.append(f"Broker: {mcfg.get('broker', 'paper')}")
        prices = {a.symbol: a.price for a in mlist}
        fxs = {a.symbol: a.fx for a in mlist}
        by_symbol = {a.symbol: a for a in mlist}

        # 1. Manage open positions: first move safety nets (profit protection), then exit when the
        #    safety net or take-profit is reached or the opening method flips.
        for event in protect_positions(broker, prices, fxs, cfg.get("protect", {}), close_hits=False):
            lines.append(f"- {event}")
        for sym, pos in list(broker.positions.items()):
            a = by_symbol.get(sym)
            if a is None or not pos.get("managed", True):
                continue  # not in the watchlist, or opened by you rather than the agent
            reason = exit_reason(pos, a)
            if reason:
                try:
                    t = broker.close(sym, a.price, reason, a.fx)
                    lines.append(f"- CLOSED {sym} @ {t['price']:.4g}: {t['note']}")
                except BrokerError as e:
                    lines.append(f"- ERROR closing {sym} ({reason}): {e}")

        # 2. New trades, best first. Each is sized against the account as it stands after earlier trades.
        busy = set(broker.positions) | broker.pending
        for c in [c for c in cands if c.market == market]:
            if c.symbol in busy:
                continue
            if methods is not None and c.view.strategy.name not in methods:
                lines.append(f"- SKIP {c.side_word} {c.symbol} (score {c.score:.0f}): "
                             f"{c.view.strategy.name} is not one of your auto-mode methods.")
                continue
            p = propose(c, broker, risk, prices, fxs)
            if not p.ok:
                lines.append(f"- SKIP {c.side_word} {c.symbol} (score {c.score:.0f}): {p.why}")
                continue
            a, qty, stop, summary = c.analysis, p.qty, p.stop, p.summary
            if mode == "review":
                print("\n" + "\n".join(_detail(c)))
                where = mcfg.get("broker", "paper")
                if ask(f"Place {where} practice order: {summary}? [y/N] ").strip().lower() != "y":
                    lines.append(f"- DECLINED {summary}")
                    continue
            try:
                t = broker.open(c.symbol, c.side, qty, a.price, stop, c.view.strategy.name, c.score, a.fx, p.target)
                lines.append(f"- {'OPENED' if t['action'] == 'OPEN' else t['action']} {summary}")
            except BrokerError as e:
                lines.append(f"- ERROR placing {summary}: {e}")

        broker.refresh()
        lines.append(f"\nEquity: {broker.equity(prices, fxs):,.2f} | cash {broker.cash:,.2f} | "
                     f"open positions: {', '.join(broker.positions) or 'none'}")

    return _write(lines, "run")


def make_broker(market: str, cfg: dict):
    """The broker named by the market's `broker:` setting (paper if absent)."""
    mcfg = cfg["markets"][market]
    kind = mcfg.get("broker", "paper")
    if kind == "paper":
        return PaperBroker(market, cfg["risk"]["starting_cash"], mcfg["fee_bps"], mcfg["slippage_bps"],
                           bool(mcfg.get("margin")))
    if kind not in BROKERS:
        raise BrokerError(f"unknown broker '{kind}' (choose paper, {', '.join(BROKERS)})")
    return BROKERS[kind](market)


def report(cfg: dict, markets: list[str] | None = None, top: int = 10) -> Path:
    analyses, cands = scan(cfg, markets)
    return _write(_header(cfg, "scan", analyses, cands, top), "scan")


def _header(cfg, mode, analyses, cands, top: int = 10) -> list[str]:
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    n_sym = sum(len(v) for v in analyses.values())
    lines = [f"# Trading agent report ({mode}), {now}", "",
             f"Analysed {n_sym} symbols x {len(methods.active_names())} methods. "
             f"{len(cands)} fresh trade ideas; minimum score to trade is {cfg['risk']['min_score']}.", "",
             "Paper trading only. Backtests use daily closes and include fees/slippage; past results do not "
             "guarantee future returns.", "", "## Market regime", "",
             "| Market | Symbol | Price | ADX | Regime | Strategies long / short / flat |", "|---|---|---|---|---|---|"]
    for market, mlist in analyses.items():
        for a in mlist:
            regime = "trending" if a.adx > 25 else "ranging" if a.adx < 20 else "mixed"
            longs = sum(v.target > 0 for v in a.views)
            shorts = sum(v.target < 0 for v in a.views)
            lines.append(f"| {market} | {a.symbol} | {a.price:.4g} | {a.adx:.0f} | {regime} | "
                         f"{longs} / {shorts} / {len(a.views) - longs - shorts} |")

    lines += ["", "## Ranked trade ideas", ""]
    if not cands:
        lines.append("No fresh signals today. Doing nothing is a valid decision.")
        return lines
    lines += ["| # | Score | Action | Symbol | Market | Strategy | Backtest |", "|---|---|---|---|---|---|---|"]
    for i, c in enumerate(cands[:top], 1):
        lines.append(f"| {i} | {c.score:.0f} | {c.side_word} | {c.symbol} | {c.market} | "
                     f"{c.view.strategy.name} | {c.view.full.short()} |")
    if len(cands) > 1:
        lines += ["", "**Why #1 over #2:** " + analyzer.compare(cands[0], cands[1])]
    lines += ["", "## Reasoning for top ideas"]
    for c in cands[:min(top, 5)]:
        lines += [""] + _detail(c)
    return lines


def _detail(c: analyzer.Candidate) -> list[str]:
    out = [f"### {c.side_word} {c.symbol} ({c.market}), score {c.score:.0f}/100",
           f"Strategy: **{c.view.strategy.name}**. {c.view.strategy.rule}",
           f"Signal fired {c.view.age} bar(s) ago at price ~{c.analysis.price:.4g}.", "", "For:"]
    out += [f"- {p}" for p in c.pros] or ["- (none)"]
    out += ["", "Against:"]
    out += [f"- {p}" for p in c.cons] or ["- (none)"]
    out += ["", "Score breakdown: " + ", ".join(f"{k} {v:+.0f}" for k, v in c.parts.items())]
    return out


def _write(lines: list[str], kind: str) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{dt.datetime.now():%Y%m%d_%H%M%S}_{kind}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
