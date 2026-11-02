"""Brokers. Phase 1 has only a local paper broker.

Each market gets its own paper account (its own state file), because real
accounts will be separate too and hold different currencies (USD, BDT, ...).
Live broker adapters (Alpaca, OANDA, Binance) implement the same interface in Phase 2.

Two accounting styles:
- cash (stocks, crypto): buying spends cash; equity = cash + qty * price.
- margin (forex): opening spends only the fee; cash moves by realised P&L.
  P&L is in the pair's quote currency, converted to the account currency by `fx`.
"""
from __future__ import annotations

import datetime as dt
import json

from .live.base import BrokerError
from .paths import HOME
from .risk import order_problem

STATE_DIR = HOME / "paper_accounts"


class PaperBroker:
    def __init__(self, market: str, starting_cash: float, fee_bps: float, slippage_bps: float,
                 margin: bool = False):
        self.market = market
        self.margin = margin
        self.fee = fee_bps / 10_000
        self.slip = slippage_bps / 10_000
        self.path = STATE_DIR / f"{market}.json"
        if self.path.exists():
            self.state = json.loads(self.path.read_text())
        else:
            self.state = {"cash": starting_cash, "positions": {}, "trades": [], "day_start": None}

    # --- account ---------------------------------------------------------
    @property
    def cash(self) -> float:
        return self.state["cash"]

    @property
    def positions(self) -> dict:
        return self.state["positions"]

    @property
    def pending(self) -> set[str]:
        return set()  # paper orders fill instantly

    def refresh(self) -> None:
        pass

    def round_qty(self, symbol: str, qty: float) -> float:
        return qty

    def exits_at_broker(self, symbol: str) -> bool:
        return False

    def value(self, symbol: str, price: float, fx: float | None = None) -> float:
        """What the position adds to equity at `price`."""
        p = self.positions[symbol]
        if self.margin:
            return p["qty"] * (price - p["entry"]) * (fx if fx is not None else p.get("fx", 1.0))
        return p["qty"] * price

    def equity(self, prices: dict[str, float], fxs: dict[str, float] | None = None) -> float:
        fxs = fxs or {}
        return self.cash + sum(self.value(s, prices.get(s, p["entry"]), fxs.get(s))
                               for s, p in self.positions.items())

    def day_start_equity(self, prices: dict[str, float], fxs: dict[str, float] | None = None) -> float:
        """Equity at the first check today; used for the daily loss limit."""
        today = dt.date.today().isoformat()
        ds = self.state.get("day_start")
        if not ds or ds["date"] != today:
            self.state["day_start"] = {"date": today, "equity": self.equity(prices, fxs)}
            self.save()
        return self.state["day_start"]["equity"]

    # --- orders ----------------------------------------------------------
    def open(self, symbol: str, side: int, qty: float, price: float, stop: float,
             strategy: str, score: float, fx: float = 1.0, target: float | None = None) -> dict:
        problem = order_problem(side, qty, price, stop, fx, target)
        if problem:
            raise BrokerError(f"Order refused: {problem}.")
        fill = price * (1 + side * self.slip)
        signed_qty = side * qty
        fee = abs(signed_qty) * fill * fx * self.fee
        self.state["cash"] -= fee if self.margin else signed_qty * fill + fee
        self.positions[symbol] = {
            "qty": signed_qty, "entry": fill, "stop": stop, "stop0": stop, "target": target,
            "strategy": strategy, "fx": fx,
            "score": score, "opened": dt.datetime.now().isoformat(timespec="seconds"),
        }
        return self._log("OPEN", symbol, signed_qty, fill, f"{strategy} (score {score:.0f})")

    def update_stop(self, symbol: str, stop: float, best: float, risk_dist: float) -> None:
        """Move the safety net (profit protection) and remember the best price so far."""
        self.positions[symbol].update(stop=stop, best=best, risk_dist=risk_dist)
        self.save()

    def close(self, symbol: str, price: float, reason: str, fx: float | None = None,
              qty: float | None = None) -> dict:
        """Close the whole position, or only `qty` units of it (a partial close)."""
        pos = self.positions[symbol]
        side = 1 if pos["qty"] > 0 else -1
        part = abs(pos["qty"]) if qty is None else min(abs(qty), abs(pos["qty"]))
        signed = side * part
        fill = price * (1 - side * self.slip)
        fx = fx if fx is not None else pos.get("fx", 1.0)
        fee = part * fill * fx * self.fee
        pnl = signed * (fill - pos["entry"]) * (fx if self.margin else 1.0)
        self.state["cash"] += (pnl if self.margin else signed * fill) - fee
        if part >= abs(pos["qty"]) - 1e-12:
            del self.positions[symbol]
        else:
            pos["qty"] -= signed
        entry_fee = part * pos["entry"] * pos.get("fx", 1.0) * self.fee
        net = pnl - fee - entry_fee
        return self._log("CLOSE", symbol, -signed, fill, f"{reason}; P&L {net:+,.2f}",
                         pnl=net, entry=pos["entry"], opened=pos.get("opened"), strategy=pos.get("strategy"),
                         cost=part * pos["entry"] * pos.get("fx", 1.0))

    def set_exits(self, symbol: str, stop: float, target: float | None) -> None:
        """Change a trade's safety net and take-profit (you set them by hand)."""
        self.positions[symbol].update(stop=stop, target=target)
        self.save()

    def _log(self, action, symbol, qty, price, note, **details) -> dict:
        t = {"time": dt.datetime.now().isoformat(timespec="seconds"), "action": action,
             "symbol": symbol, "qty": qty, "price": price, "note": note, **details}
        self.state["trades"].append(t)
        self.save()
        return t

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state, indent=2))
