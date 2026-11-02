# Trading agent: technical notes

A rule-based trading agent for four markets: US stocks, crypto, forex and the
Dhaka Stock Exchange. It runs every strategy on every symbol, backtests each one
on that symbol's own history, scores the fresh signals, explains its reasoning,
and trades **practice accounts only**: a local paper account, or Alpaca paper,
OANDA practice and Binance testnet. Real-money endpoints are not in the code.

## Setup

Most people should use the dashboard: double-click `Start Trading Agent.bat`.
It creates the Python environment in `.venv` on first run.

The command line uses the same environment, run from this folder:

```powershell
.venv\Scripts\python.exe -m trader scan
```

Code layout: `trader/` (agent, strategies, backtester, risk manager, brokers in `trader/live/`,
dashboard in `trader/dashboard/`), `tests/`, settings in `config.yaml`, keys in `.env`.
`python tests/dashboard_test.py` exercises the dashboard's approve/close/pause flow.

## Connecting practice accounts (Phase 2)

Every market starts on the local paper broker. To move one to a real practice account:

1. Open the account (all free, email sign-up):
   - **Alpaca paper** (US stocks): https://app.alpaca.markets → switch to *Paper* → *API Keys* → *Generate*.
   - **OANDA practice** (forex): open a demo account → *Manage API Access* → generate a token.
     Copy the account ID too (looks like `101-001-1234567-001`). The account currency must be USD.
   - **Binance spot testnet** (crypto): https://testnet.binance.vision → log in with GitHub →
     *Generate HMAC_SHA256 Key*.
2. Copy `.env.example` to `.env` and paste the keys in. Never paste them into a chat.
3. Test the connection: `python -m trader check --broker alpaca` (or `oanda`, `binance`).
4. In `config.yaml`, set that market's `broker:` to `alpaca`, `oanda` or `binance`.
5. Run `python -m trader run` (review mode asks before every order).

How each practice broker is handled:

| | Alpaca paper | OANDA practice | Binance testnet |
|---|---|---|---|
| Stop-loss | Placed at Alpaca with the entry (OTO order) | Placed at OANDA with the entry | Checked by the agent when it runs |
| Shorting | Yes (config has it off for stocks) | Yes | No (spot) |
| Daily loss base | Alpaca's previous-close equity | First run of the day | First run of the day |
| Notes | Orders outside market hours wait for the open; the agent tracks them as pending | Account must be in USD | Testnet's pre-loaded coins are ignored; only coins the agent buys count |

Positions you open yourself in these accounts are shown but never touched by the agent.
The agent's own records (which strategy opened what, and the stop) live in
`paper_accounts/<market>_<broker>_journal.json`.

## Commands

| Command | What it does |
|---|---|
| `python -m trader scan` | Analyse all markets, write a ranked report to `reports/`. No trading. |
| `python -m trader scan --market forex` | Same, one market (repeatable). |
| `python -m trader run` | One agent cycle. In `review` mode it shows each trade's reasoning and asks y/N. |
| `python -m trader run --mode auto` | One cycle placing practice trades automatically. |
| `python -m trader check` | Test each market's broker connection (`--broker X` tests one not yet in use). |
| `python -m trader backtest SPY --market us_stocks` | Compare every strategy on one symbol vs buy-and-hold. |
| `python -m trader portfolio` | Show local paper accounts, positions and recent trades. |
| `python -m trader kill on` / `off` | Kill switch: blocks all new trades. |
| `python tests/smoke_test.py` | End-to-end check on a throwaway local paper account. |
| `python tests/test_brokers.py` | Offline tests of the Alpaca/OANDA/Binance adapters with a fake API. |

Settings (watchlists, brokers, fees, risk limits, mode) are in `config.yaml`.

## How it decides

1. **Strategies** (`trader/strategies.py`): SMA 50/200 crossover, EMA 12/26 crossover,
   MACD with trend filter, RSI mean reversion, Bollinger band reversion,
   Donchian 20-day breakout, 6-month momentum.
2. **Backtest** (`trader/backtest.py`): every strategy on each symbol's full history and
   on the last year, with fees and slippage per market.
3. **Fresh signals only**: a trade idea exists when a strategy's signal flipped in the
   last `fresh_signal_bars` bars.
4. **Score 0-100** (`trader/analyzer.py`): long-run backtest quality, whether it still
   works recently, edge over buy-and-hold, how many other strategies agree, and fit with
   the market regime (ADX: trending vs ranging). Penalties for few trades or deep drawdowns.
5. **Reasoning**: each idea lists its arguments for and against, a score breakdown, and why
   #1 ranks above #2.
6. **Risk manager** (`trader/risk.py`) has the final say: minimum score, 1% equity risk per
   trade with an ATR-based stop, 20% max position, max 8 positions, 3% daily loss limit,
   and the kill switch.
7. **Exits**: a position closes when its strategy no longer signals that direction or the
   stop is hit.

## Known limitations

- Daily bars only; the agent runs when you run it. Scheduling comes later.
- The adapters are tested against a fake API built from each broker's docs, not yet
  against real practice accounts. Run `check`, then a review-mode `run`, before using auto mode.
- Binance testnet stops are only checked when the agent runs, not continuously.
- On the local paper broker, forex margin requirements and overnight swap costs are not
  modelled; on OANDA practice they are real.
- Free DSE data covers only about 2 years, so DSE backtests have few trades and score low.
- Real-money trading is disabled (`live_trading: false`, and no live URLs in the code).
- Backtest results are not a forecast. Most strategies here only roughly match or trail
  buy-and-hold on stocks, which the scores reflect.

## Roadmap

- **Phase 2** (built): Alpaca paper, OANDA practice and Binance testnet adapters.
- **Phase 3**: DSE review mode with a BO account (you place the orders yourself).
