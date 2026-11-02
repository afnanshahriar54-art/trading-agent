"""Command line: python -m trader <command>"""
from __future__ import annotations

import argparse
import json

from . import backtest, data
from .agent import load_config, make_broker, report, run
from .broker import STATE_DIR
from .live import BrokerError
from .risk import KILL_SWITCH
from .methods import available
from .strategies import positions


def main():
    p = argparse.ArgumentParser(prog="trader", description="Multi-market rule-based trading agent (Phase 1: paper)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="analyse all markets and write a ranked report (no trading)")
    s.add_argument("--market", action="append", help="limit to a market (repeatable)")
    s.add_argument("--top", type=int, default=10)

    r = sub.add_parser("run", help="one agent cycle on the paper accounts")
    r.add_argument("--market", action="append")
    r.add_argument("--mode", choices=["review", "auto"], help="override config.yaml mode")

    b = sub.add_parser("backtest", help="compare every strategy on one symbol")
    b.add_argument("symbol")
    b.add_argument("--market", required=True)

    sub.add_parser("portfolio", help="show paper accounts")
    c = sub.add_parser("check", help="test the connection to each market's broker")
    c.add_argument("--broker", action="append", choices=["alpaca", "oanda", "binance"],
                   help="test a broker even if config.yaml does not use it yet")
    k = sub.add_parser("kill", help="turn the kill switch on/off (blocks all new trades)")
    k.add_argument("state", choices=["on", "off"])

    args = p.parse_args()
    cfg = load_config()

    if args.cmd == "scan":
        path = report(cfg, args.market, args.top)
        print(path.read_text(encoding="utf-8"))
        print(f"\nSaved: {path}")
    elif args.cmd == "run":
        path = run(cfg, args.mode or cfg["mode"], args.market)
        print(path.read_text(encoding="utf-8"))
        print(f"\nSaved: {path}")
    elif args.cmd == "backtest":
        mcfg = cfg["markets"][args.market]
        df = data.load(args.symbol, args.market, cfg["history_years"])
        print(f"{args.symbol}: {len(df)} daily bars {df.index[0]:%Y-%m-%d} to {df.index[-1]:%Y-%m-%d}\n")
        bh, _ = backtest.run(df, df.close * 0 + 1, 0, 0)
        print(f"{'Buy and hold':28s} {bh.short()}")
        for st in available():
            m, _ = backtest.run(df, positions(st, df, mcfg["allow_short"]), mcfg["fee_bps"], mcfg["slippage_bps"])
            print(f"{st.name:28s} {m.short()}")
    elif args.cmd == "portfolio":
        files = sorted(STATE_DIR.glob("*.json")) if STATE_DIR.exists() else []
        if not files:
            print("No paper accounts yet. Run: python -m trader run")
        for f in files:
            st = json.loads(f.read_text())
            print(f"\n[{f.stem}] cash {st['cash']:,.2f}")
            for sym, pos in st["positions"].items():
                print(f"  {sym}: qty {pos['qty']:g} @ {pos['entry']:.4g}, stop {pos['stop']:.4g}, {pos['strategy']}")
            for t in st["trades"][-5:]:
                print(f"  {t['time']} {t['action']} {t['symbol']} {t['qty']:g} @ {t['price']:.4g}: {t['note']}")
    elif args.cmd == "check":
        targets = [(m, mc.get("broker", "paper")) for m, mc in cfg["markets"].items()]
        default_market = {"alpaca": "us_stocks", "oanda": "forex", "binance": "crypto"}
        targets += [(default_market[b], b) for b in args.broker or [] if (default_market[b], b) not in targets]
        for market, kind in targets:
            try:
                broker = make_broker(market, {**cfg, "markets": {**cfg["markets"],
                                                                 market: {**cfg["markets"][market], "broker": kind}}})
                pos = broker.positions
                managed = [s for s, p in pos.items() if p.get("managed", True)]
                print(f"OK    {market:10s} {kind:8s} cash {broker.cash:,.2f}, "
                      f"{len(managed)} agent positions, {len(pos) - len(managed)} other")
            except (BrokerError, OSError) as e:
                print(f"FAIL  {market:10s} {kind:8s} {e}")
    elif args.cmd == "kill":
        if args.state == "on":
            KILL_SWITCH.write_text("Trading halted. Delete this file or run: python -m trader kill off\n")
        elif KILL_SWITCH.exists():
            KILL_SWITCH.unlink()
        print(f"Kill switch {args.state.upper()}")


if __name__ == "__main__":
    main()
