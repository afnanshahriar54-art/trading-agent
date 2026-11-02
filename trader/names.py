"""Human-readable names for markets and symbols."""

MARKETS = {
    "us_stocks": "US Stocks",
    "crypto": "Crypto",
    "forex": "Forex",
    "dse": "Dhaka Stock Exchange",
}

BROKERS = {
    "paper": "Practice account (on this computer)",
    "alpaca": "Alpaca practice account",
    "oanda": "OANDA practice account",
    "binance": "Binance test account",
}

SYMBOLS = {
    "SPY": "S&P 500 index fund", "QQQ": "Nasdaq 100 index fund", "AAPL": "Apple", "MSFT": "Microsoft",
    "NVDA": "Nvidia", "AMZN": "Amazon", "GOOGL": "Google (Alphabet)", "META": "Meta (Facebook)",
    "JPM": "JPMorgan Chase", "XOM": "ExxonMobil",
    "BTC-USD": "Bitcoin", "ETH-USD": "Ethereum", "SOL-USD": "Solana", "BNB-USD": "BNB", "XRP-USD": "XRP",
    "EURUSD=X": "Euro / US Dollar", "GBPUSD=X": "British Pound / US Dollar", "USDJPY=X": "US Dollar / Japanese Yen",
    "AUDUSD=X": "Australian Dollar / US Dollar", "USDCHF=X": "US Dollar / Swiss Franc",
    "GP": "Grameenphone", "SQURPHARMA": "Square Pharmaceuticals", "BATBC": "British American Tobacco Bangladesh",
    "BEXIMCO": "Beximco", "BRACBANK": "BRAC Bank",
}

UNITS = {"us_stocks": "shares", "dse": "shares", "crypto": "coins", "forex": "units"}


def symbol(s: str) -> str:
    return SYMBOLS.get(s, s)


def market(m: str) -> str:
    return MARKETS.get(m, m)
