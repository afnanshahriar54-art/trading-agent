"""A small web app on this computer only (http://127.0.0.1:8765).

It shows the accounts, finds trade ideas, explains them in plain language and
places the ones you approve. It uses the same agent, risk manager and brokers
as the command line.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pandas as pd

from .. import agent, analyzer, builder, daily, data, fx, methods, names, odds, performance, protect, replay, settings, track
from .. import indicators as ind
from ..broker import STATE_DIR
from ..live import BrokerError
from ..paths import HOME
from ..risk import KILL_SWITCH, RiskConfig, RiskManager
from ..strategies import STRATEGIES

HOST, PORT = "127.0.0.1", 8765
PAGE = Path(__file__).with_name("index.html")
VENDOR = {"/vendor/lightweight-charts.js": Path(__file__).with_name("vendor") / "lightweight-charts.js"}
CURRENCY = {"dse": "BDT"}  # everything else is USD


def confidence_label(score: float, min_score: float) -> str:
    if score >= 70:
        return "Strong"
    if score >= min_score:
        return "Good"
    if score >= 40:
        return "Weak"
    return "Very weak"


class App:
    def __init__(self):
        self.lock = threading.Lock()
        self.scan_status = {"running": False, "done": 0, "total": 0, "current": "", "finished_at": None, "error": None}
        self.analyses: dict[str, list[analyzer.SymbolAnalysis]] = {}
        self.ideas: dict[str, analyzer.Candidate] = {}
        self.odds_cache: dict[tuple, dict | None] = {}
        self.order: list[str] = []
        self.last_seen: float | None = None  # last heartbeat from an open window
        self.report_status = {"running": False, "step": "", "error": None}
        self.daily_running = False
        self.chart_cache: dict[tuple, tuple] = {}
        self.method_status = {"running": False, "step": "", "error": None}
        self.trade_lock = threading.Lock()
        self.live_cache: tuple[float, list] = (0.0, [])  # (when read, open positions) for live P&L  # one order at a time (you, the agent's approvals, the live watcher)

    def ping(self) -> dict:
        self.last_seen = time.time()
        return {"ok": True}

    @property
    def cfg(self) -> dict:
        return agent.load_config()  # re-read so edits to config.yaml apply without a restart

    # --- scanning ----------------------------------------------------------
    def start_scan(self) -> dict:
        with self.lock:
            if self.scan_status["running"]:
                return self.scan_status
            self.scan_status.update(running=True, done=0, total=0, current="Starting...", error=None)
        threading.Thread(target=self._scan, daemon=True).start()
        return self.scan_status

    def _scan(self):
        def progress(i, total, market, symbol):
            self.scan_status.update(done=i, total=total,
                                    current=f"{names.symbol(symbol)} ({names.market(market)})")
        try:
            analyses, cands = agent.scan(self.cfg, verbose=False, progress=progress)
            with self.lock:
                self.analyses = analyses
                self.ideas = {f"{c.market}:{c.symbol}:{c.side}": c for c in cands}
                self.order = list(self.ideas)
            self.scan_status.update(done=self.scan_status["total"], current="",
                                    finished_at=dt.datetime.now().strftime("%d %b %Y, %I:%M %p"))
        except Exception as e:
            self.scan_status["error"] = str(e)
        finally:
            self.scan_status["running"] = False

    def analysis(self, market: str, symbol: str) -> analyzer.SymbolAnalysis | None:
        return next((a for a in self.analyses.get(market, []) if a.symbol == symbol), None)

    def price_fx(self, market: str, symbol: str) -> tuple[float, float]:
        a = self.analysis(market, symbol)
        if a:
            return a.price, a.fx
        # Latest price, including today's unfinished day: this is for showing and closing positions.
        price = float(data.load(symbol, market, self.cfg["history_years"], finished_only=False).close.iloc[-1])
        return price, fx.quote_to_usd(symbol, price) if self.cfg["markets"][market].get("margin") else 1.0

    # --- views -------------------------------------------------------------
    def overview(self) -> dict:
        cfg = self.cfg
        accounts = []
        for market, mcfg in cfg["markets"].items():
            acc = {"market": market, "name": names.market(market), "currency": CURRENCY.get(market, "USD"),
                   "broker": names.BROKERS.get(mcfg.get("broker", "paper"), mcfg.get("broker"))}
            try:
                broker = agent.make_broker(market, cfg)
                prices, fxs, positions = {}, {}, []
                for sym, pos in broker.positions.items():
                    prices[sym], fxs[sym] = self.price_fx(market, sym)
                for sym, pos in broker.positions.items():
                    price, f = prices[sym], fxs[sym]
                    side = 1 if pos["qty"] > 0 else -1
                    pnl = pos["qty"] * (price - pos["entry"]) * (f if broker.margin else 1.0)
                    a = self.analysis(market, sym)
                    suggestion = agent.exit_reason(pos, a) if a and pos.get("managed", True) else None
                    positions.append({
                        "symbol": sym, "name": names.symbol(sym), "side": "Bought" if side > 0 else "Sold short",
                        "qty": abs(pos["qty"]), "units": names.UNITS.get(market, "units"),
                        "entry": pos["entry"], "price": price, "stop": pos.get("stop"), "stop0": pos.get("stop0"), "target": pos.get("target"),
                        "fx": f if broker.margin else 1.0, "dir": side,
                        "pnl": pnl, "pnl_pct": side * (price / pos["entry"] - 1) * 100 if pos["entry"] else 0,
                        "method": pos.get("strategy"), "managed": pos.get("managed", True),
                        "suggest_close": suggestion,
                    })
                equity = broker.equity(prices, fxs)
                start = broker.day_start_equity(prices, fxs)
                acc.update(ok=True, equity=equity, cash=broker.cash, today=equity - start,
                           today_pct=(equity / start - 1) * 100 if start else 0,
                           positions=positions, pending=sorted(broker.pending))
            except Exception as e:
                acc.update(ok=False, error=str(e))
            accounts.append(acc)
        performance.record_snapshot({a["market"]: a["equity"] for a in accounts if a.get("ok")})
        risk = cfg["risk"]
        return {"accounts": accounts, "paused": KILL_SWITCH.exists(), "live_trading": bool(cfg.get("live_trading")),
                "rules": {"risk_per_trade_pct": risk["risk_per_trade_pct"], "max_position_pct": risk["max_position_pct"],
                          "max_open_positions": risk["max_open_positions"],
                          "daily_loss_limit_pct": risk["daily_loss_limit_pct"], "min_score": risk["min_score"]}}

    def ideas_view(self) -> dict:
        cfg = self.cfg
        rm = RiskManager(RiskConfig(**cfg["risk"]))
        brokers, out = {}, []
        for key in self.order:
            c = self.ideas[key]
            a = c.analysis
            item = {
                "id": key, "market": c.market, "market_name": names.market(c.market), "symbol": c.symbol,
                "name": names.symbol(c.symbol), "action": "Buy" if c.side > 0 else "Sell short",
                "score": round(c.score), "label": confidence_label(c.score, cfg["risk"]["min_score"]),
                "method": c.view.strategy.name, "rule": c.view.strategy.rule,
                "signal_days": c.view.age, "price": a.price, "currency": CURRENCY.get(c.market, "USD"),
                "units": names.UNITS.get(c.market, "units"), "pros": c.pros, "cons": c.cons,
                "record": {"return": c.view.full.total_return, "win_rate": c.view.full.win_rate,
                           "trades": c.view.full.trades, "worst_drop": c.view.full.max_drawdown,
                           "last_year": c.view.recent.total_return, "hold_return": a.bh_return,
                           "years": round(a.years)},
            }
            try:
                if c.market not in brokers:
                    brokers[c.market] = agent.make_broker(c.market, cfg)
                b = brokers[c.market]
                if c.symbol in b.positions or c.symbol in b.pending:
                    item["proposal"] = {"ok": False, "why": "You already have a trade open in this."}
                else:
                    p = agent.propose(c, b, rm, {c.symbol: a.price}, {c.symbol: a.fx})
                    equity = b.equity({c.symbol: a.price}, {c.symbol: a.fx})
                    item["proposal"] = {"ok": p.ok, "why": p.why, "qty": p.qty, "stop": p.stop,
                                        "risk": p.risk_amount, "risk_pct": p.risk_amount / equity * 100 if equity else 0,
                                        "size": p.notional, "cost": p.cost,
                                        **self._target_info(p.target, a.price, p.qty, a.fx, cfg["markets"][c.market]),
                                        "odds": self._odds(c.market, c.symbol, a.price, p.stop, p.target, c.side, p.qty, a.fx)}
            except Exception as e:
                item["proposal"] = {"ok": False, "why": f"Account not reachable: {e}"}
            out.append(item)
        cands = [self.ideas[k] for k in self.order]
        compare = analyzer.compare(cands[0], cands[1]) if len(cands) > 1 else None
        return {"status": self.scan_status, "ideas": out, "compare": compare,
                "min_score": cfg["risk"]["min_score"], "protect": cfg.get("protect", {})}

    def results(self) -> dict:
        try:
            ideas_track = track.review(self.cfg)
        except Exception as e:
            ideas_track = {"error": str(e)}
        return {"practice": performance.summary(), "report": replay.load_saved(), "track": ideas_track,
                "report_status": self.report_status}

    def start_report(self) -> dict:
        with self.lock:
            if self.report_status["running"]:
                return self.report_status
            self.report_status.update(running=True, step="Starting...", error=None)

        def work():
            try:
                replay.run_all(self.cfg, progress=lambda step: self.report_status.update(step=step))
            except Exception as e:
                self.report_status["error"] = str(e)
            finally:
                self.report_status["running"] = False
        threading.Thread(target=work, daemon=True).start()
        return self.report_status

    CHART_TTL = {"minute": 10, "day": 15, "week": 60, "month": 300, "year": 300}  # seconds before prices are re-fetched

    def _chart_df(self, market: str, symbol: str, rng: str):
        if market not in self.cfg["markets"] or rng not in data.CHART_RANGES:
            raise BrokerError("Unknown chart.")
        key = (market, symbol, rng)
        cached = self.chart_cache.get(key)
        if cached and time.time() - cached[0] < self.CHART_TTL[rng]:
            return cached[1]
        try:
            df = data.chart(symbol, market, rng)
        except Exception as e:
            raise BrokerError(str(e))
        self.chart_cache[key] = (time.time(), df)
        return df

    def live_price(self, market: str, symbol: str) -> tuple[float, str, bool]:
        """(latest price, when, is it live): the live quote, or the last daily close for Dhaka."""
        if market == "dse":
            df = data.load(symbol, market, finished_only=False)
            return float(df.close.iloc[-1]), df.index[-1].strftime("%d %b %Y"), False
        q = data.live_quote(symbol, market, max_age=3 if market == "forex" else 1)
        return q["price"], pd.Timestamp.now(tz="Asia/Dhaka").strftime("%d %b %H:%M:%S"), q["live"]

    def quote(self, market: str, symbol: str) -> dict:
        """Latest price for the newest chart candle. time_key uses the chart's clock (Bangladesh time)."""
        if market not in self.cfg["markets"]:
            raise BrokerError("Unknown market.")
        price, when, live = self.live_price(market, symbol)
        now = pd.Timestamp.now(tz="Asia/Dhaka").tz_localize(None)
        return {"price": price, "when": when, "live": live,
                "time_key": int(now.tz_localize("UTC").timestamp())}

    def live_trades(self) -> dict:
        """Every open trade with its current profit/loss, refreshed each second by the Open trades page.
        Positions are re-read from the brokers every 5 seconds; prices every second (forex every 3)."""
        if time.time() - self.live_cache[0] > 5:
            cfg, held = self.cfg, []
            for market in cfg["markets"]:
                try:
                    b = agent.make_broker(market, cfg)
                    held += [(market, sym, dict(pos), b.margin) for sym, pos in b.positions.items()]
                except Exception:
                    continue
            self.live_cache = (time.time(), held)
        held = self.live_cache[1]

        def one(item):
            market, sym, pos, margin = item
            try:
                price, when, live = self.live_price(market, sym)
            except Exception:
                return None
            f = fx.quote_to_usd(sym, price) if margin else 1.0
            side = 1 if pos["qty"] > 0 else -1
            return {"key": f"{market}:{sym}", "market": market, "symbol": sym, "price": price, "when": when, "live": live,
                    "pnl": pos["qty"] * (price - pos["entry"]) * f,
                    "pnl_pct": side * (price / pos["entry"] - 1) * 100 if pos["entry"] else 0,
                    "currency": CURRENCY.get(market, "USD")}

        with ThreadPoolExecutor(max_workers=8) as pool:  # fetch all prices at once
            rows = [r for r in pool.map(one, held) if r]
        return {"trades": rows, "total_usd": sum(r["pnl"] for r in rows if r["currency"] == "USD"),
                "total_bdt": sum(r["pnl"] for r in rows if r["currency"] == "BDT"),
                "time": pd.Timestamp.now(tz="Asia/Dhaka").strftime("%H:%M:%S")}

    def chart(self, market: str, symbol: str, range: str = "month") -> dict:
        df = self._chart_df(market, symbol, range)
        intraday = range in ("minute", "day", "week") and market != "dse"
        age_min = (pd.Timestamp.now(tz="Asia/Dhaka").tz_localize(None) - df.index[-1]).total_seconds() / 60
        # time: seconds for intraday (Bangladesh clock time shown as-is), a date for daily bars.
        bars = [{"t": ts.strftime("%d %b %H:%M" if intraday else "%d %b %Y"),
                 "time": int(pd.Timestamp(ts).tz_localize("UTC").timestamp()) if intraday else ts.strftime("%Y-%m-%d"),
                 "o": r.open, "h": r.high, "l": r.low, "c": r.close} for ts, r in df.iterrows()]
        lines = []
        try:
            pos = agent.make_broker(market, self.cfg).positions.get(symbol)
            if pos:
                lines.append({"label": "You bought at" if pos["qty"] > 0 else "You sold short at", "v": pos["entry"]})
                if pos.get("stop"):
                    lines.append({"label": "Safety net", "v": pos["stop"]})
                if pos.get("target"):
                    lines.append({"label": "Take-profit", "v": pos["target"]})
        except Exception:
            pass
        first, last = df.close.iloc[0], df.close.iloc[-1]
        return {"symbol": symbol, "name": names.symbol(symbol), "market": names.market(market), "range": range,
                "bars": bars, "lines": lines, "last": float(last), "change_pct": float((last / first - 1) * 100),
                "currency": CURRENCY.get(market, "USD"),
                "times_in": "Bangladesh time" if intraday else None,
                "live": intraday and age_min < 20, "last_time": df.index[-1].strftime("%d %b %H:%M"),
                "source": df.attrs.get("source", "Daily closing prices")}

    # --- live watcher: take-profit and safety net on the practice accounts on this PC ---
    def start_watcher(self, every: int = 30) -> None:
        def loop():
            while True:
                try:
                    self.watch_once()
                except Exception as e:
                    print(f"{time.ctime()}: watcher error: {e}")
                time.sleep(every)
        threading.Thread(target=loop, daemon=True).start()

    def watch_once(self) -> list[str]:
        """With live prices: move safety nets (profit protection), then close any trade whose
        safety net or take-profit was reached. Alpaca and OANDA close trades themselves; for them
        this only moves the safety-net order at the broker."""
        cfg, events = self.cfg, []
        protect_on = any(v.get("on") for v in cfg.get("protect", {}).values())
        for market, mcfg in cfg["markets"].items():
            if mcfg.get("broker", "paper") in ("alpaca", "oanda") and not protect_on:
                continue  # the broker handles the exits and there is nothing to move
            with self.trade_lock:
                try:
                    b = agent.make_broker(market, cfg)
                    held = [s for s, p in b.positions.items() if p.get("managed", True)]
                except Exception:
                    continue
                prices, fxs = {}, {}
                for sym in held:
                    try:
                        prices[sym] = self.live_price(market, sym)[0]
                    except Exception:
                        continue
                    fxs[sym] = fx.quote_to_usd(sym, prices[sym]) if b.margin else 1.0
                try:
                    events += protect.manage(b, prices, fxs, cfg.get("protect", {}))
                except Exception as e:
                    print(f"{time.ctime()}: watcher could not manage {market}: {e}")
        closed = [e for e in events if "safety net moved" not in e]
        if events:
            print(f"{time.ctime()}: watcher: {events}")
        if closed:
            daily.notify("Trading Agent", ("Closed " + "; ".join(closed))[:250])
        return closed

    # --- your own trades from the chart --------------------------------------
    def manual_quote(self, market: str, symbol: str, side: int, amount: float | None = None) -> dict:
        """Size and check a trade you picked yourself, with the same safety rules as the agent."""
        cfg = self.cfg
        mcfg = cfg["markets"].get(market)
        if mcfg is None or symbol not in mcfg["symbols"]:
            raise BrokerError("Unknown asset.")
        side = 1 if int(side) > 0 else -1
        amount = float(amount) if amount else None
        if amount is not None and amount <= 0:
            raise BrokerError("Enter an amount above zero.")
        if side < 0 and not mcfg["allow_short"]:
            raise BrokerError(f"Selling short is turned off for {names.market(market)} in your settings.")
        price, when, live = self.live_price(market, symbol)
        daily_df = data.load(symbol, market, cfg["history_years"])
        atr = float(ind.atr(daily_df).iloc[-1])
        b = agent.make_broker(market, cfg)
        margin = b.margin
        f = fx.quote_to_usd(symbol, price) if margin else 1.0
        rm = RiskManager(RiskConfig(**cfg["risk"]))
        equity = b.equity({symbol: price}, {symbol: f})
        stop = rm.stop_price(price, atr, side)
        suggested = rm.size(equity, price, stop, f)

        def rounded(q: float) -> float:
            """The smallest steps each market really trades in."""
            if market == "dse":
                return float(int(q))              # the Dhaka exchange trades whole shares only
            if market == "us_stocks":
                # Alpaca buys parts of a share (from $1), but sells short whole shares only.
                return float(int(q)) if side < 0 else math.floor(q * 1e6) / 1e6
            if margin:
                return float(int(q))              # forex: down to 1 unit
            return b.round_qty(symbol, q)

        qty = rounded(amount / (price * f) if amount else suggested)
        size, risk = qty * price * f, qty * abs(price - stop) * f
        cost = size if side > 0 and not margin else 0.0
        max_risk = equity * rm.cfg.risk_per_trade_pct / 100
        max_size = equity * rm.cfg.max_position_pct / 100
        day_start = b.day_start_equity({symbol: price}, {symbol: f})
        # Hard stops: things the broker or the app can't do. Everything else is your call: a warning.
        ok, why = True, "ok"
        if KILL_SWITCH.exists():
            ok, why = False, "Trading is paused (the stop switch is on). Switch it off on the Home page."
        elif symbol in b.positions or symbol in b.pending:
            ok, why = False, "You already have a trade open in this. Close it first, or close part of it."
        elif qty <= 0:
            ok, why = False, {"dse": "The Dhaka exchange only trades whole shares. Enter at least the price of one share.",
                              "us_stocks": ("Short selling needs at least one whole share." if side < 0
                                            else "Alpaca's smallest trade is $1.")}.get(market, "That amount is too small for this market.")
        elif market == "us_stocks" and side > 0 and not qty.is_integer() and size < 0.99:  # $1, less rounding
            ok, why = False, "Alpaca's smallest trade is $1."
        elif cost > b.cash:
            ok, why = False, f"Not enough cash in this account: {cost:,.2f} needed, {b.cash:,.2f} available."
        warnings = []
        if ok:
            if risk > max_risk * 1.001:
                warnings.append(f"It could lose about {risk:,.0f}, more than your limit of {max_risk:,.0f} "
                                f"({rm.cfg.risk_per_trade_pct:g}% of the account).")
            if size > max_size * 1.001:
                warnings.append(f"It's bigger than your limit of {max_size:,.0f} per trade ({rm.cfg.max_position_pct:g}% of the account).")
            if len(b.positions) >= rm.cfg.max_open_positions:
                warnings.append(f"You already have {len(b.positions)} open trades here (your limit is {rm.cfg.max_open_positions}).")
            day = (equity / day_start - 1) * 100 if day_start else 0
            if day <= -rm.cfg.daily_loss_limit_pct:
                warnings.append(f"This account is down {-day:.1f}% today, past your {rm.cfg.daily_loss_limit_pct:g}% daily limit.")
            if market == "us_stocks" and not qty.is_integer() and mcfg.get("broker") == "alpaca":
                warnings.append("Part of a share: Alpaca can't hold the safety net for it, so this app watches it "
                                "(every 30 seconds while open, and at the daily check), not Alpaca around the clock.")
        return {"ok": ok, "why": why, "warnings": warnings, "side": side, "qty": qty, "price": price,
                "price_time": when, "live": live,
                "stop": stop, "size": size, "risk": risk, "risk_pct": risk / equity * 100 if equity else 0,
                "suggested_size": rounded(suggested) * price * f, "max_risk": max_risk,
                "units": names.UNITS.get(market, "units"), "currency": CURRENCY.get(market, "USD"),
                "account": names.BROKERS.get(mcfg.get("broker", "paper")), "fx": f, "protect": cfg.get("protect", {}),
                **self._target_info(rm.target_price(price, stop, side, qty, f), price, qty, f, mcfg,
                                    whole=float(qty).is_integer()),
                "odds": self._odds(market, symbol, price, stop, rm.target_price(price, stop, side, qty, f), side, qty, f)}

    def _odds(self, market: str, symbol: str, price: float, stop: float, target: float | None, side: int,
              qty: float, f: float) -> dict | None:
        """10,000 practice runs of this exact trade (cached: the same trade gives the same answer)."""
        key = (market, symbol, round(price, 8), round(stop, 8), target and round(target, 8), side, qty,
               json.dumps(self.cfg.get("protect"), sort_keys=True))
        if key not in self.odds_cache:
            try:
                df = data.load(symbol, market, self.cfg["history_years"])
                self.odds_cache[key] = odds.check(df, market, price, stop, target, side, qty, f, self.cfg.get("protect"))
            except Exception:
                return None
            if len(self.odds_cache) > 300:
                self.odds_cache.pop(next(iter(self.odds_cache)))
        return self.odds_cache[key]

    def _target_info(self, target: float | None, price: float, qty: float, f: float, mcfg: dict,
                     whole: bool = True) -> dict:
        """The take-profit price, the gain if it's reached, and whether the broker or this app closes it."""
        if target is None:
            return {"target": None}
        at_broker = mcfg.get("broker", "paper") in ("alpaca", "oanda") and whole
        return {"target": target, "gain": abs(target - price) * qty * f,
                "target_how": "placed at the broker, closes instantly even when this app is closed" if at_broker
                else "checked every 30 seconds while this app is open, and at the daily check"}

    def manual_trade(self, market: str, symbol: str, side: int, amount: float | None = None,
                     accept: bool = False) -> str:
        """`accept`: you saw the warnings (bigger than your limits, etc.) and want the trade anyway."""
        if self.cfg.get("live_trading"):
            raise BrokerError("Real-money trading is switched off in this version.")
        q = self.manual_quote(market, symbol, side, amount)
        if not q["ok"]:
            raise BrokerError(q["why"])
        if q["warnings"] and not accept:
            raise BrokerError("Please confirm the warnings first: " + " ".join(q["warnings"]))
        b = agent.make_broker(market, self.cfg)
        t = b.open(symbol, q["side"], q["qty"], q["price"], q["stop"], agent.MANUAL, 0, q["fx"], q.get("target"))
        what = f"{'bought' if q['side'] > 0 else 'sold short'} {q['qty']:g} {q['units']} of {names.symbol(symbol)}"
        if t["action"] != "OPEN":
            return f"Order sent: {what}. The market is closed, so it fills when it opens."
        tp = f", take-profit at {q['target']:,.4g}" if q.get("target") else ""
        return f"Done: {what} at about {t['price']:,.4g}. Safety net at {q['stop']:,.4g}{tp}."

    def symbols(self) -> list[dict]:
        return [{"market": m, "market_name": names.market(m), "symbol": s, "name": names.symbol(s)}
                for m, mcfg in self.cfg["markets"].items() for s in mcfg["symbols"]]

    def settings_view(self) -> dict:
        cfg = self.cfg
        report = replay.load_saved() or {"markets": []}
        card = {m["market"]: m for m in report["markets"] if m.get("ok")}
        method_pnl: dict[str, float] = {}
        for m in card.values():
            for x in m["methods"]:
                method_pnl[x["method"]] = method_pnl.get(x["method"], 0) + x["pnl"]
        return {
            "settings": settings.load(), "scheduled": daily.is_scheduled(), "last": daily.last_run(),
            "checking": self.daily_running,
            "rules": {k: cfg["risk"][k] for k in settings.RULE_LIMITS},
            "limits": settings.RULE_LIMITS, "rule_names": settings.RULE_NAMES,
            "markets": [{"id": m, "name": names.market(m), "broker": names.BROKERS.get(mc.get("broker", "paper")),
                         "report": card[m]["agent"]["total_return"] if m in card else None,
                         "years": card[m]["years"] if m in card else None}
                        for m, mc in cfg["markets"].items()],
            "methods": [{"name": s.name, "rule": s.rule, "report_pnl": method_pnl.get(s.name)} for s in methods.active()],
        }

    # --- methods page -------------------------------------------------------
    @staticmethod
    def best_tested(card: dict) -> list[str]:
        """Methods with a steady record (good reward for the risk, Sharpe 0.5+) in both US stocks and crypto."""
        return [n for n, mk in card.get("methods", {}).items()
                if all(mk.get(m, {}).get("sharpe", 0) >= 0.5 for m in ("us_stocks", "crypto"))]

    def methods_view(self) -> dict:
        card = replay.load_method_card() or {"methods": {}}
        best = set(self.best_tested(card))
        enabled = set(settings.enabled_methods())
        source = {s.name: ("Original" if i < 7 else "Well-known" if i < 13 else "From the strike.money article")
                  for i, s in enumerate(STRATEGIES)}
        return {
            "methods": [{"name": s.name, "family": builder.FAMILIES.get(s.family, "Momentum"), "rule": s.rule,
                         "source": source.get(s.name, "Yours"), "custom": s.name not in source,
                         "enabled": s.name in enabled, "card": card["methods"].get(s.name), "best": s.name in best}
                        for s in methods.available()],
            "conditions": builder.CONDITIONS, "families": builder.FAMILIES, "max_conditions": builder.MAX_CONDITIONS,
            "card_created": card.get("created"), "card_status": self.method_status,
            "market_names": {m: names.market(m) for m in self.cfg["markets"]},
        }

    def change_methods(self, body: dict) -> dict:
        s = settings.load()
        enabled, custom = list(settings.enabled_methods(s)), list(s["methods"]["custom"])
        action, name = body.get("action"), body.get("name")
        if action == "toggle":
            if name not in {m.name for m in methods.available()}:
                raise BrokerError("Unknown method.")
            enabled = [n for n in enabled if n != name] + ([name] if body.get("on") else [])
            if not enabled:
                raise BrokerError("Keep at least one method switched on.")
        elif action == "preset":
            card = replay.load_method_card() or replay.method_card(self.cfg)
            enabled = {"original": list(settings.DEFAULT_ENABLED), "best": self.best_tested(card),
                       "all": [m.name for m in methods.available()]}.get(body.get("preset"))
            if not enabled:
                raise BrokerError("Unknown choice.")
        elif action == "add":
            try:
                m = builder.validate(body.get("method", {}), {x.name for x in methods.available()})
            except ValueError as e:
                raise BrokerError(str(e))
            custom.append(m)  # saved switched off: test it first, then switch it on
        elif action == "delete":
            custom = [m for m in custom if m["name"] != name]
            enabled = [n for n in enabled if n != name]
        else:
            raise BrokerError("Unknown action.")
        settings.save_methods(enabled, custom)
        return {"ok": True}

    def test_method(self, body: dict) -> dict:
        try:
            m = builder.validate({**body, "name": body.get("name") or "Test"}, set())
        except ValueError as e:
            raise BrokerError(str(e))
        return {"rule": builder.compile(m).rule, "results": replay.method_results(builder.compile(m), self.cfg)}

    def start_method_card(self) -> dict:
        if not self.method_status["running"]:
            self.method_status.update(running=True, step="Starting...", error=None)

            def work():
                try:
                    replay.method_card(self.cfg, progress=lambda step: self.method_status.update(step=step))
                except Exception as e:
                    self.method_status["error"] = str(e)
                finally:
                    self.method_status["running"] = False
            threading.Thread(target=work, daemon=True).start()
        return self.method_status

    def save_settings(self, body: dict) -> dict:
        try:
            s = settings.save(body, list(self.cfg["markets"]))
        except ValueError as e:
            raise BrokerError(str(e))
        daily.schedule(s["check"]["enabled"], s["check"]["time"])
        return s

    def run_daily_now(self) -> dict:
        if not self.daily_running:
            self.daily_running = True

            def work():
                try:
                    daily.check()
                finally:
                    self.daily_running = False
            threading.Thread(target=work, daemon=True).start()
        return {"checking": True}

    def history(self) -> list[dict]:
        rows = []
        for f in STATE_DIR.glob("*.json") if STATE_DIR.exists() else []:
            market = next((m for m in names.MARKETS if f.stem.startswith(m)), f.stem)
            for t in json.loads(f.read_text()).get("trades", []):
                rows.append({**t, "market": names.market(market), "name": names.symbol(t["symbol"])})
        rows.sort(key=lambda t: t["time"], reverse=True)
        return rows[:100]

    # --- actions -----------------------------------------------------------
    def approve(self, idea_id: str) -> str:
        c = self.ideas.get(idea_id)
        if c is None:
            raise BrokerError("This idea is out of date. Please find trade ideas again.")
        cfg = self.cfg
        if cfg.get("live_trading"):
            raise BrokerError("Real-money trading is switched off in this version.")
        b = agent.make_broker(c.market, cfg)
        if c.symbol in b.positions or c.symbol in b.pending:
            raise BrokerError("You already have a trade open in this.")
        a = c.analysis
        p = agent.propose(c, b, RiskManager(RiskConfig(**cfg["risk"])), {c.symbol: a.price}, {c.symbol: a.fx})
        if not p.ok:
            raise BrokerError(p.why)
        t = b.open(c.symbol, c.side, p.qty, a.price, p.stop, c.view.strategy.name, c.score, a.fx, p.target)
        if t["action"] != "OPEN":
            return (f"Order sent for {p.qty:g} {names.symbol(c.symbol)}. The market is closed, so it will fill "
                    f"when it opens.")
        return f"Done: {'bought' if c.side > 0 else 'sold short'} {p.qty:g} {names.symbol(c.symbol)} at about {t['price']:,.4g}."

    def close(self, market: str, symbol: str, percent: float = 100) -> str:
        """Close a trade now at the live price, fully or partly (percent of it)."""
        b = agent.make_broker(market, self.cfg)
        pos = b.positions.get(symbol)
        if pos is None:
            raise BrokerError("That trade is no longer open.")
        percent = float(percent)
        if not 0 < percent <= 100:
            raise BrokerError("Choose how much to close (1 to 100%).")
        price, _, _ = self.live_price(market, symbol)
        f = fx.quote_to_usd(symbol, price) if b.margin else 1.0
        part = None
        if percent < 100:
            part = abs(pos["qty"]) * percent / 100
            if market == "us_stocks" and pos["qty"] > 0:
                part = math.floor(part * 1e6) / 1e6  # parts of a share are fine when selling what you own
            elif market in ("us_stocks", "dse") or b.margin:
                part = float(int(part))  # whole shares / units
            else:
                part = b.round_qty(symbol, part)
            if part <= 0:
                raise BrokerError("That's less than one unit. Close a bigger part, or all of it.")
            if part >= abs(pos["qty"]):
                part = None
        total = abs(pos["qty"])  # before closing: a partial close changes it
        t = b.close(symbol, price, "closed by you", f, part)
        self.live_cache = (0.0, [])
        what = "Closed" if part is None else f"Closed {part:g} of {total:g} {names.UNITS.get(market, 'units')} of"
        return f"{what} {names.symbol(symbol)}: {t['note'].split('; ')[-1]}."

    def close_many(self, which: str = "all") -> str:
        """Close all open trades, or only the ones currently winning or losing."""
        if which not in ("all", "winners", "losers"):
            raise BrokerError("Unknown choice.")
        done, failed = [], []
        for t in self.live_trades()["trades"]:
            if (which == "winners" and t["pnl"] <= 0) or (which == "losers" and t["pnl"] >= 0):
                continue
            try:
                self.close(t["market"], t["symbol"])
                done.append(t)
            except Exception as e:
                failed.append(f"{names.symbol(t['symbol'])} ({e})")
        self.live_cache = (0.0, [])
        if not done and not failed:
            return {"all": "No open trades.", "winners": "No trade is in profit right now.",
                    "losers": "No trade is losing right now."}[which]
        msg = f"Closed {len(done)} trade{'s' if len(done) != 1 else ''}"
        usd = sum(t["pnl"] for t in done if t["currency"] == "USD")
        msg += f" (about {'+' if usd >= 0 else '-'}${abs(usd):,.2f})." if done else "."
        return msg + (f" Could not close: {', '.join(failed)}." if failed else "")

    def set_exits(self, market: str, symbol: str, stop: float, target: float | None) -> str:
        """Change a trade's safety net and take-profit by hand, within your safety rules."""
        b = agent.make_broker(market, self.cfg)
        pos = b.positions.get(symbol)
        if pos is None:
            raise BrokerError("That trade is no longer open.")
        side = 1 if pos["qty"] > 0 else -1
        price, _, _ = self.live_price(market, symbol)
        stop = float(stop)
        target = float(target) if target not in (None, "", 0) else None
        first = pos.get("stop0") or pos.get("stop")
        if side * (price - stop) <= 0:
            raise BrokerError(f"The safety net must be {'below' if side > 0 else 'above'} today's price ({price:,.4g}), "
                              "or it would close the trade straight away. Use Close now instead.")
        if first and side * (first - stop) > 1e-9:
            raise BrokerError(f"You can move the safety net closer, but not further away than where it started "
                              f"({first:,.4g}): that would risk more than your rules allow.")
        if target is not None and side * (target - price) <= 0:
            raise BrokerError(f"The take-profit must be {'above' if side > 0 else 'below'} today's price ({price:,.4g}).")
        b.set_exits(symbol, stop, target)
        self.live_cache = (0.0, [])
        tp = f", take-profit {target:,.4g}" if target else ", take-profit off"
        return f"{names.symbol(symbol)}: safety net {stop:,.4g}{tp}."

    def set_paused(self, on: bool) -> None:
        if on:
            KILL_SWITCH.write_text("Trading paused from the dashboard.\n")
        elif KILL_SWITCH.exists():
            KILL_SWITCH.unlink()


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # Browsers drop connections all the time (closing a window, reloading); that's not an error.
        if not isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    app: App

    def log_message(self, *args):  # keep the console quiet
        pass

    def _send(self, code: int, body, ctype="application/json"):
        raw = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _local(self) -> bool:
        # Refuse requests that arrive under another host name (DNS rebinding protection).
        return self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost")

    def do_GET(self):
        if not self._local():
            return self._send(403, {"error": "forbidden"})
        routes = {
            "/": lambda: PAGE.read_bytes(),
            "/api/overview": self.app.overview,
            "/api/ideas": self.app.ideas_view,
            "/api/history": self.app.history,
            "/api/results": self.app.results,
            "/api/settings": self.app.settings_view,
            "/api/methods": self.app.methods_view,
            "/api/symbols": self.app.symbols,
            "/api/chart": lambda: self.app.chart(**dict(parse_qsl(urlsplit(self.path).query))),
            "/api/ping": self.app.ping,
            "/api/live": self.app.live_trades,
            "/api/quote": lambda: self.app.quote(**dict(parse_qsl(urlsplit(self.path).query))),
        }
        if self.path in VENDOR:  # chart library, shipped with the app
            return self._send(200, VENDOR[self.path].read_bytes(), "application/javascript")
        fn = routes.get(self.path.split("?")[0])
        if fn is None:
            return self._send(404, {"error": "not found"})
        try:
            result = fn()
            self._send(200, result, "text/html; charset=utf-8" if self.path == "/" else "application/json")
        except Exception as e:
            self._send(500, {"error": str(e)})

    def do_POST(self):
        # The custom header can't be sent by other websites without permission, which blocks
        # a malicious page from placing orders through this app.
        if not self._local() or self.headers.get("X-Trading-Agent") != "1":
            return self._send(403, {"error": "forbidden"})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        try:
            if self.path == "/api/manual-quote":
                return self._send(200, self.app.manual_quote(body["market"], body["symbol"], body["side"], body.get("amount")))
            if self.path == "/api/manual-trade":
                with self.app.trade_lock:
                    msg = self.app.manual_trade(body["market"], body["symbol"], body["side"], body.get("amount"),
                                                bool(body.get("accept")))
                return self._send(200, {"message": msg})
            if self.path == "/api/methods":
                return self._send(200, self.app.change_methods(body))
            if self.path == "/api/method-test":
                return self._send(200, self.app.test_method(body))
            if self.path == "/api/method-card":
                return self._send(200, self.app.start_method_card())
            if self.path == "/api/settings":
                return self._send(200, {"settings": self.app.save_settings(body)})
            if self.path == "/api/daily-run":
                return self._send(200, self.app.run_daily_now())
            if self.path == "/api/report-card":
                return self._send(200, self.app.start_report())
            if self.path == "/api/scan":
                return self._send(200, self.app.start_scan())
            if self.path == "/api/approve":
                with self.app.trade_lock:
                    msg = self.app.approve(body["id"])
                return self._send(200, {"message": msg})
            if self.path == "/api/close":
                with self.app.trade_lock:
                    msg = self.app.close(body["market"], body["symbol"], body.get("percent", 100))
                return self._send(200, {"message": msg})
            if self.path == "/api/close-many":
                with self.app.trade_lock:
                    msg = self.app.close_many(body.get("which", "all"))
                return self._send(200, {"message": msg})
            if self.path == "/api/set-exits":
                with self.app.trade_lock:
                    msg = self.app.set_exits(body["market"], body["symbol"], body["stop"], body.get("target"))
                return self._send(200, {"message": msg})
            if self.path == "/api/open-folder":
                os.startfile(HOME)  # opens File Explorer on the settings folder
                return self._send(200, {"ok": True})
            if self.path == "/api/pause":
                self.app.set_paused(bool(body.get("on")))
                return self._send(200, {"paused": KILL_SWITCH.exists()})
            self._send(404, {"error": "not found"})
        except BrokerError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": f"Something went wrong: {e}"})


def main(open_browser: bool = True):
    """Console mode (Start Trading Agent.bat): server in this window, page in the default browser."""
    Handler.app = App()
    Handler.app.start_watcher()
    server = Server((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"Trading Agent is running at {url}\nKeep this window open. Close it to stop the app.")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
