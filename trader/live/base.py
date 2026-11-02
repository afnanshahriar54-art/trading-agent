"""Shared behaviour for broker adapters.

The broker is the source of truth for cash and positions. A local journal remembers
what only the agent knows: which strategy opened a position and where its stop is.
Positions the agent did not open (e.g. ones you placed by hand) are reported with
managed=False and are never closed by the agent.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import requests

from ..paths import HOME as ROOT
STATE_DIR = ROOT / "paper_accounts"
ENV_FILE = ROOT / ".env"


class BrokerError(Exception):
    pass


def load_env(path: Path | None = None) -> None:
    """Read KEY=VALUE lines from .env into the environment (existing variables win)."""
    path = path or ENV_FILE
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def require_keys(*names: str) -> list[str]:
    load_env()
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise BrokerError(f"missing {', '.join(missing)} in .env (copy .env.example to .env and fill it in)")
    return [os.environ[n] for n in names]


class LiveBroker:
    name = "live"
    margin = False
    broker_side_exits = False  # True when the broker itself holds the safety net and take-profit orders

    def __init__(self, market: str, session: requests.Session | None = None):
        self.market = market
        self.http = session or requests.Session()
        self.journal_path = STATE_DIR / f"{market}_{self.name}_journal.json"
        if self.journal_path.exists():
            self.journal = json.loads(self.journal_path.read_text())
        else:
            self.journal = {"positions": {}, "trades": [], "day_start": None}
        self._cache: dict | None = None

    # --- implemented by each adapter ---------------------------------------
    def _account(self) -> dict:
        """{'cash': float, 'equity': float | None, 'day_start': float | None}"""
        raise NotImplementedError

    def _broker_positions(self) -> dict[str, tuple[float, float]]:
        """{our_symbol: (signed qty, average entry price)}"""
        raise NotImplementedError

    def _submit(self, symbol: str, side: int, qty: float, stop: float,
                target: float | None = None) -> tuple[float, float, str | None]:
        """Place a market order (with a broker-side stop and take-profit where supported).
        Returns (filled qty, fill price or 0, order id if still waiting to fill else None)."""
        raise NotImplementedError

    def _order_open(self, order_id: str) -> bool:
        """True while an order is still waiting to fill (e.g. placed outside market hours)."""
        return False

    def _close(self, symbol: str, qty: float, part: float | None = None) -> float:
        """Flatten the position (signed `qty`), or only `part` units of it.
        Returns the fill price, or 0 if unknown."""
        raise NotImplementedError

    def round_qty(self, symbol: str, qty: float) -> float:
        return qty

    # --- interface used by the agent (same as PaperBroker) -----------------
    def refresh(self) -> None:
        self._cache = None

    def _state(self) -> dict:
        if self._cache is None:
            acct = self._account()
            held = self._broker_positions()
            journal = self.journal["positions"]
            for sym in [s for s in journal if s not in held]:
                order_id = journal[sym].get("pending_order")
                if order_id and self._order_open(order_id):
                    continue  # still waiting to fill
                # Gone at the broker: closed there (e.g. its stop filled) or the entry order died.
                note = ("entry order did not fill" if order_id
                        else "closed at the broker (safety net or take-profit reached)")
                self._log("CLOSED AT BROKER", sym, -journal[sym]["qty"], 0.0, note)
                del journal[sym]
            for sym in journal:
                if sym in held:
                    journal[sym].pop("pending_order", None)
            positions = {}
            for sym, (qty, avg) in held.items():
                j = journal.get(sym)
                positions[sym] = {
                    "qty": qty, "entry": avg or (j or {}).get("entry", 0.0),
                    "stop": j["stop"] if j else None, "target": j.get("target") if j else None,
                    "stop0": j.get("stop0") if j else None, "best": j.get("best") if j else None,
                    "risk_dist": j.get("risk_dist") if j else None,
                    "strategy": j["strategy"] if j else "manual",
                    "fx": j.get("fx", 1.0) if j else 1.0, "managed": j is not None,
                }
            self._save()
            self._cache = {"account": acct, "positions": positions}
        return self._cache

    @property
    def cash(self) -> float:
        return self._state()["account"]["cash"]

    @property
    def positions(self) -> dict:
        return self._state()["positions"]

    @property
    def pending(self) -> set[str]:
        """Symbols with an entry order sent but not yet filled."""
        self._state()
        return {s for s, j in self.journal["positions"].items() if j.get("pending_order")}

    def equity(self, prices: dict[str, float], fxs: dict[str, float] | None = None) -> float:
        acct = self._state()["account"]
        if acct.get("equity") is not None:
            return acct["equity"]
        return self.cash + sum(p["qty"] * prices.get(s, p["entry"]) for s, p in self.positions.items())

    def day_start_equity(self, prices: dict[str, float], fxs: dict[str, float] | None = None) -> float:
        acct = self._state()["account"]
        if acct.get("day_start"):
            return acct["day_start"]
        today = dt.date.today().isoformat()
        ds = self.journal.get("day_start")
        if not ds or ds["date"] != today:
            self.journal["day_start"] = {"date": today, "equity": self.equity(prices, fxs)}
            self._save()
        return self.journal["day_start"]["equity"]

    def open(self, symbol: str, side: int, qty: float, price: float, stop: float,
             strategy: str, score: float, fx: float = 1.0, target: float | None = None) -> dict:
        filled, fill, pending_order = self._submit(symbol, side, qty, stop, target)
        fill = fill or price
        entry = {
            "qty": side * (filled or qty), "entry": fill, "stop": stop, "stop0": stop, "target": target,
            "strategy": strategy, "fx": fx,
            "score": score, "opened": dt.datetime.now().isoformat(timespec="seconds"),
        }
        if pending_order:
            entry["pending_order"] = pending_order
        if self.broker_side_exits and not self.whole(qty):
            entry["app_exits"] = True
        self.journal["positions"][symbol] = entry
        self.refresh()
        action = "ORDER SENT (waiting to fill)" if pending_order else "OPEN"
        return self._log(action, symbol, entry["qty"], fill, f"{strategy} (score {score:.0f}) via {self.name}")

    @staticmethod
    def whole(qty: float) -> bool:
        return abs(qty - round(qty)) < 1e-9

    def exits_at_broker(self, symbol: str) -> bool:
        """Does the broker hold this trade's safety net and take-profit? (Not for fractional shares,
        which Alpaca can't attach exit orders to: the app watches those itself.)"""
        return self.broker_side_exits and not (self.journal["positions"].get(symbol) or {}).get("app_exits")

    def _move_stop(self, symbol: str, stop: float) -> None:
        """Move the broker-side safety-net order (brokers that hold exits override this)."""

    def update_stop(self, symbol: str, stop: float, best: float, risk_dist: float) -> None:
        j = self.journal["positions"].get(symbol)
        if j is None:
            return
        if self.exits_at_broker(symbol) and abs(stop - j["stop"]) > 1e-9:
            self._move_stop(symbol, stop)
        j.update(stop=stop, best=best, risk_dist=risk_dist)
        self._save()
        self.refresh()

    def close(self, symbol: str, price: float, reason: str, fx: float | None = None,
              qty: float | None = None) -> dict:
        """Close the whole position, or only `qty` units of it (a partial close)."""
        pos = self.positions[symbol]
        side = 1 if pos["qty"] > 0 else -1
        part = None if qty is None or abs(qty) >= abs(pos["qty"]) - 1e-12 else abs(qty)
        fill = self._close(symbol, pos["qty"], part) or price
        signed = side * (part if part is not None else abs(pos["qty"]))
        j = self.journal["positions"].get(symbol) or {}
        opened = j.get("opened")
        if part is None:
            self.journal["positions"].pop(symbol, None)
        elif j:
            j["qty"] = pos["qty"] - signed  # what the broker held, minus what was just sold
        self._save()
        self.refresh()
        f = fx if fx is not None else pos.get("fx", 1.0)
        pnl = signed * (fill - pos["entry"]) * f  # before broker fees
        return self._log("CLOSE", symbol, -signed, fill, f"{reason}; P&L ~{pnl:+,.2f}",
                         pnl=pnl, entry=pos["entry"], opened=opened, strategy=pos.get("strategy"),
                         cost=abs(signed) * pos["entry"] * f)

    def _set_exits(self, symbol: str, qty: float, stop: float, target: float | None) -> None:
        """Replace the broker-side safety net / take-profit (brokers that hold exits override this)."""

    def set_exits(self, symbol: str, stop: float, target: float | None) -> None:
        """Change a trade's safety net and take-profit (you set them by hand)."""
        j = self.journal["positions"].get(symbol)
        if j is None:
            raise BrokerError("This trade wasn't opened by the app, so change it at the broker.")
        if self.exits_at_broker(symbol):
            self._set_exits(symbol, self.positions[symbol]["qty"], stop, target)
        j.update(stop=stop, target=target)
        self._save()
        self.refresh()

    # --- helpers ---------------------------------------------------------
    def _check(self, r: requests.Response, what: str) -> dict:
        if r.status_code >= 400:
            raise BrokerError(f"{self.name} {what}: HTTP {r.status_code} {r.text[:300]}")
        return r.json() if r.content else {}

    def _log(self, action, symbol, qty, price, note, **details) -> dict:
        t = {"time": dt.datetime.now().isoformat(timespec="seconds"), "action": action,
             "symbol": symbol, "qty": qty, "price": price, "note": note, **details}
        self.journal["trades"].append(t)
        self._save()
        return t

    def _save(self) -> None:
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        self.journal_path.write_text(json.dumps(self.journal, indent=2))
