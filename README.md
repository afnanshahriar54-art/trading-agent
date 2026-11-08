# Trading Agent: start here

## Open it

Double-click the **Trading Agent** icon on your Desktop, or find it in the Start menu.
It opens in its own window. Close the window when you're done; the app stops by itself
a few minutes later.

(Backup way: double-click `Start Trading Agent.bat` in this folder. That opens it in your
web browser with a black window you keep open.)

## Install it on another PC

Copy **`build\TradingAgentSetup.exe`** (about 41 MB) to the other PC, e.g. on a USB stick,
and double-click it. It installs for that Windows user (no administrator needed), adds
Desktop and Start menu icons, and appears in Settings > Apps for uninstalling. Python is
not needed on that PC.

A new install starts with all four markets on practice accounts on that computer. To connect
Alpaca there, use **How it works > Open settings folder** in the app and follow `README.txt`.

Windows may show a blue "Windows protected your PC" box the first time, because the file
isn't signed by a known publisher. Click **More info**, then **Run anyway**.

To rebuild the setup file after changing the app:
`powershell -ExecutionPolicy Bypass -File packaging\build.ps1`

## Use it

1. **Home**: your four practice accounts and how much each is worth.
2. **Trade ideas**: click **Find trade ideas**. In about 10 seconds the agent checks 25 assets
   (US stocks, crypto, forex, Dhaka stocks) with 7 methods and lists what it found, best first.
   Each idea tells you in plain words:
   - what it would buy or sell, and how much
   - its **safety net**: the price at which it closes automatically
   - the **most you'd likely lose**
   - reasons for and against, and a confidence score out of 100
3. Click **Approve trade** on the ideas you agree with. Nothing is traded without your click.
   Each idea has a **See chart** button.
4. **Charts**: any asset's price as candles, with **Day** (5-minute candles), **Week**,
   **Month** and **Year** views. Day and Week update by themselves while the market is open
   (a green **Live** badge shows). US stock prices come live from your Alpaca connection.
   Hover to see exact prices. Your purchase price and safety net show as dashed lines.
   (Dhaka stocks: Month and Year only.)
   Below the chart, **Trade** lets you **Buy** or **Sell short** yourself: enter an amount
   (or use the suggested size), check the plan, safety net and maximum loss, then place it.
   Your own trades follow the same safety rules, and only their safety net closes them.
5. **Methods**: the 20 rules the agent can use to find ideas: the original 7, 6 well-known
   ones (Supertrend, Ichimoku cloud, Triple average, Volume breakout, Stochastic dip, Squeeze
   breakout) and 7 from the strike.money trading-strategies article (Range bounce, Hammer in
   uptrend, Fibonacci pullback, Seasonal months, Gap fill, Deep dip (RSI-2), RSI divergence).
   - Each shows its test results per market against buying and holding (**Test all methods**).
   - Switch methods **on/off**. Only switched-on methods produce trade ideas. New ones start off.
   - **Build your own method**: pick "buy when" conditions (all must be true) and "sell when"
     conditions (any one), click **Test it**, then **Save** and switch it on.
6. **Settings**:
   - **Manual mode** (recommended): nothing is traded without you.
   - **Auto mode**: choose the markets and methods the app may use (each shows its
     report-card result), and it places and closes practice trades by itself at the daily check.
   - **Daily check**: a time each day (08:00 suggested) when the agent checks all markets, even
     with the app closed, and shows a Windows notification if there's something to do.
   - **Safety rules**: minimum confidence, most a trade can lose, biggest trade, most open
     trades, daily loss limit. They apply to every trade in both modes.
   - **Take profit**: close a winning trade automatically at 2× what it risked (default), a % gain,
     or a money amount, or Off. At Alpaca the target sits at the broker and closes instantly, even
     with the app closed. On the practice accounts on this PC, a live watcher checks targets and
     safety nets every 30 seconds while the app is open (and at the daily check), and shows a
     Windows notification when it closes a trade. Trade-off: more wins and smaller drops, but less
     total profit, because big winners get cut short.
   - **Protect profits (lower risk)**: a **trailing safety net** follows the best price, staying a set
     distance behind it (1.5× what the trade risked did best in the report card), and/or a
     **break-even** safety net moves to the buy price once a trade is up. Safety nets only move in
     your favour. At Alpaca the safety-net order itself is moved.
   - Safety nets and take-profits on the practice accounts on this PC are also enforced at the daily
     check, in both modes, so they work even when the app was closed.
7. **Open trades**: check once a day. If the agent thinks a trade should be closed, it says so
   next to that trade, and you click **Close**.
8. **Results**: is the agent any good?
   - *Your practice trades*: how many closed trades won, total profit or loss, average win
     vs. average loss, and a chart of your accounts against the S&P 500.
   - *Report card*: click **Run report card** to replay the agent's exact rules over the past
     ~4 years (using only prices it would have known each day) and compare with simply
     buying and holding. It also splits your own trades from the agent's, so you can see who does better.
9. **History**: every order, newest first.

Most days there will be few or no strong ideas. That's normal, and doing nothing is fine.

**Pause all trading** on the Home page blocks every new trade until you resume.

## Look and feel

- **Light / Dark / Auto** switch at the top (Auto follows Windows). Your choice is remembered.
- **Charts**: smooth zoom (mouse wheel) and scrolling (drag), a crosshair with exact prices,
  **Candles** or **Line** view, and live updates that extend the chart instead of redrawing it.
- Every trade idea, the chart's Trade panel and every open trade show three plain numbers:
  **you put in**, **if it goes well**, **if it goes wrong**. Details are under **Why this idea?**
- **Methods**: the Trade ideas page shows which methods are switched on. On the Methods page,
  one click picks **Best tested** (the 7 marked ✓, which held up in both US stocks and crypto),
  **Original 7** or **All**.

## Live trading screens

- **Open trades** updates every second while it's open: each trade's profit/loss and price, and the
  total at the top. Ways to close: **Close all of it**, **Close part** (25%, half, 75%),
  **Change safety net / take-profit** (the safety net can move closer, never further than where it
  started), and at the top **Close all winning / all losing / all trades**.
- **Charts** has a **1 min** view (one candle per minute); in 1 min and Day views the newest
  candle moves with the live price every 2 seconds. Live prices: Alpaca for US stocks, Binance or
  Coinbase for crypto, Yahoo for forex (every few seconds). Dhaka stocks have no live feed.
- **Settings** has a search bar: type e.g. "stop loss", "risk", "daily check" or "dark".

## Odds check and track record

- **Odds check** (under every trade idea, and in the Charts trade panel): the exact trade, with your safety net,
  trailing and take-profit, is played 10,000 times on random real days from the asset's last 2 years. It shows how
  often it reached the take-profit, how often it hit the safety net, and the average result. If a trade wins often
  but loses money on average (small wins, big losses), it says so. It's the past replayed at random, not a prediction.
- **Track record of trade ideas** (Results page): every idea the app suggests is written down
  (`reports/idea_log.jsonl`) and followed for about a month against the real prices, whether you took it or not.
  It shows how many made money and compares with simply buying the same assets.

## Small trades (any amount)

- **Charts page, trade panel:** quick buttons for **$5, $10, $25, $50**, or type any amount.
  - US stocks: parts of a share from $1 (Alpaca's minimum). Short selling needs whole shares.
  - Forex: down to **1 unit** of the base currency (EUR in EUR/USD, USD in USD/JPY).
  - Crypto: small fractions. Dhaka shares: whole shares only.
- **Forex preview** shows: units, actual size in dollars (never more than you typed: always
  rounded down), the loss if the safety net is hit, trading costs in and out, overnight
  financing per day, the **margin** (deposit the broker holds, about 3.3% of the size on the
  practice account, *not* the trade's size), and your free margin.
- **Refused, with the reason:** less than the pair's smallest trade, not enough free margin,
  trading paused, a trade already open in that pair, broken prices.
- **Going past your safety limits** (risk per trade, size, open trades, daily loss) shows a
  warning; you can still place it after confirming.
- **Trade ideas and Auto mode** (Settings, 7. Forex trade size): size forex trades by risk
  (as before) or as a small fixed amount, e.g. $25. A fixed amount is never allowed to risk
  more than the risk rule would.
- With a **dollar take-profit** (e.g. $250), small trades can't realistically reach it, so it
  acts like "off" for them. A take-profit "times the risk" scales with the trade instead.

## Your accounts

| Market | Where trades go |
|---|---|
| US Stocks | Your Alpaca practice account (connected) |
| Crypto | Practice account on this computer (Binance test account can be connected later) |
| Forex | Practice account on this computer (OANDA practice account can be connected later) |
| Dhaka Stock Exchange | Practice account on this computer |

All of these use **practice money**. Real-money trading is switched off.

## Safety rules (defaults; change them in Settings)

- A trade can lose at most **1%** of its account before its safety net closes it.
- No trade is bigger than **20%** of the account.
- At most **8** open trades per account.
- If an account falls **3%** in a day, no new trades that day.
- Only ideas with confidence **55 or higher** can be approved.

These live in `config.yaml` if you ever want to change them.

## Important to know

- The methods are tested on past prices. **Past results don't guarantee future ones.**
- Prices are daily closing prices, so the app is for checking once a day, not minute-by-minute.
- Keep `.env` private. It holds your Alpaca keys. Never share it or paste it anywhere.

## Folder contents

| Item | What it is |
|---|---|
| `Start Trading Agent.bat` | Backup way to open the app (in the browser) |
| `build\TradingAgentSetup.exe` | Installer for other PCs |
| `packaging/` | How the installer is made (icon, install scripts, build script) |
| `README.md` | This guide |
| `config.yaml` | Settings: which assets, which accounts, safety rules |
| `.env` | Your private account keys |
| `trader/` | The program itself |
| `docs/TECHNICAL.md` | Technical details and command-line use |
| `tests/` | Automatic checks |
| `paper_accounts/`, `data_cache/`, `reports/` | Saved accounts, price data and old reports (created automatically) |
