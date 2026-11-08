"""Forex trade sizing: dollars -> units, the broker's size rules, margin, and costs.

Kept apart from order execution so the same rules size your own trades, the agent's
ideas and the report card. Amounts use Decimal so rounding is exact, and units are
always rounded DOWN: an order never ends up bigger than the amount you asked for.

Words used here:
- units: how much of the BASE currency (EUR in EUR/USD, USD in USD/JPY) is bought or sold.
- notional: what those units are worth in US dollars (the trade's real size).
- margin: the deposit the broker holds while the trade is open = notional x margin rate.
  A $10 trade at a 3.33% margin rate needs about $0.33 of margin, not $10.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation

PRESETS_USD = (5, 10, 25, 50)
DEFAULT_MARGIN_RATE = "0.0333"   # 30:1 leverage, a cautious figure for major pairs


@dataclass(frozen=True)
class Spec:
    """A pair's trade-size rules. At OANDA these come from its instruments list
    (minimumTradeSize, tradeUnitsPrecision, maximumOrderUnits, marginRate)."""
    min_units: Decimal = Decimal(1)
    precision: int = 0                    # decimal places allowed in the units
    max_units: Decimal = Decimal(100_000_000)
    margin_rate: Decimal = Decimal(DEFAULT_MARGIN_RATE)


def spec_for(broker, symbol: str, mcfg: dict) -> Spec:
    """The broker's own rules when it publishes them, else this app's practice-account rules."""
    get = getattr(broker, "instrument_spec", None)
    if get is not None:
        try:
            return get(symbol)
        except Exception:
            pass  # fall back to the cautious defaults below
    return Spec(margin_rate=_dec(mcfg.get("margin_rate", DEFAULT_MARGIN_RATE)))


def _dec(v) -> Decimal:
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{v!r} is not a number")
    if not d.is_finite():
        raise ValueError(f"{v!r} is not a finite number")
    return d


def usd_per_unit(price: float, quote_to_usd: float) -> Decimal:
    """What one unit of the base currency is worth in US dollars.
    EUR/USD at 1.10 -> $1.10; USD/JPY -> $1 (price x 1/price); EUR/GBP -> price x GBP/USD."""
    p, q = _dec(price), _dec(quote_to_usd)
    if p <= 0 or q <= 0:
        raise ValueError("price and currency rate must be above zero")
    return p * q


def normalize(units, spec: Spec) -> Decimal:
    """Round DOWN to the broker's step and cap at its maximum; below its minimum -> 0."""
    u = _dec(units)
    if u <= 0:
        return Decimal(0)
    step = Decimal(1).scaleb(-spec.precision)
    u = min(u, spec.max_units).quantize(step, rounding=ROUND_DOWN)
    return u if u >= spec.min_units else Decimal(0)


def units_for_usd(usd, price: float, quote_to_usd: float, spec: Spec) -> Decimal:
    """The most units whose value does not exceed `usd` dollars."""
    amount = _dec(usd)
    if amount <= 0:
        return Decimal(0)
    return normalize(amount / usd_per_unit(price, quote_to_usd), spec)


@dataclass
class Plan:
    """Everything the trade preview shows, in US dollars."""
    units: Decimal
    notional: Decimal          # what the units are worth
    margin: Decimal            # deposit the broker holds while it's open
    stop_loss: Decimal         # lost if the safety net is hit (before costs)
    costs: Decimal             # fees + spread in and out
    financing_day: Decimal     # overnight financing per day held
    smallest_usd: Decimal      # the smallest trade this pair allows, in dollars

    def as_dict(self) -> dict:
        return {k: float(v) for k, v in self.__dict__.items()}


def plan(units, price: float, stop: float, quote_to_usd: float, spec: Spec, mcfg: dict) -> Plan:
    u = _dec(units)
    per_unit = usd_per_unit(price, quote_to_usd)
    notional = u * per_unit
    stop_loss = u * abs(_dec(price) - _dec(stop)) * _dec(quote_to_usd)
    cost_rate = (_dec(mcfg.get("fee_bps", 0)) + _dec(mcfg.get("slippage_bps", 0))) / 10_000
    return Plan(
        units=u, notional=notional, margin=notional * spec.margin_rate, stop_loss=stop_loss,
        costs=notional * cost_rate * 2,
        financing_day=notional * _dec(mcfg.get("carry_pct_year", 0)) / 100 / 365,
        smallest_usd=spec.min_units * per_unit,
    )


def margin_used(positions: dict, prices: dict, fxs: dict, spec_of) -> Decimal:
    """Margin held by the open forex trades (practice accounts, which don't report it)."""
    total = Decimal(0)
    for sym, p in positions.items():
        px = prices.get(sym, p.get("entry", 0))
        f = fxs.get(sym, p.get("fx", 1.0))
        try:
            total += abs(_dec(p["qty"])) * usd_per_unit(px, f) * spec_of(sym).margin_rate
        except ValueError:
            continue
    return total


def margin_available(broker, equity: float, prices: dict, fxs: dict, spec_of) -> Decimal:
    """Money left for new margin: the broker's own figure when it gives one (OANDA), else
    account value minus the margin the open trades already hold."""
    get = getattr(broker, "margin_available", None)
    if get is not None:
        try:
            return _dec(get())
        except Exception:
            pass
    return _dec(equity) - margin_used(broker.positions, prices, fxs, spec_of)
