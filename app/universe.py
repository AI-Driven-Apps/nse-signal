"""Trading universe: Nifty 50 (.NS Yahoo Finance suffixes).
Keeping the universe small keeps ingest + nightly strategy runs fast on the Pi.
Edit freely, or later load from the watchlist table."""

NIFTY50 = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS",
    "BHARTIARTL.NS", "SBIN.NS", "LICI.NS", "ITC.NS", "HINDUNILVR.NS",
    "LT.NS", "BAJFINANCE.NS", "HCLTECH.NS", "MARUTI.NS", "SUNPHARMA.NS",
    "KOTAKBANK.NS", "AXISBANK.NS", "ULTRACEMCO.NS", "NTPC.NS", "TITAN.NS",
    "ASIANPAINT.NS", "ADANIENT.NS", "ONGC.NS", "ADANIPORTS.NS", "WIPRO.NS",
    "POWERGRID.NS", "M&M.NS", "COALINDIA.NS", "BAJAJFINSV.NS", "NESTLEIND.NS",
    "TATAMOTORS.NS", "TATASTEEL.NS", "JSWSTEEL.NS", "GRASIM.NS", "HDFCLIFE.NS",
    "SBILIFE.NS", "TECHM.NS", "HINDALCO.NS", "BRITANNIA.NS", "EICHERMOT.NS",
    "DRREDDY.NS", "CIPLA.NS", "DIVISLAB.NS", "APOLLOHOSP.NS", "TATACONSUM.NS",
    "INDUSINDBK.NS", "BAJAJ-AUTO.NS", "HEROMOTOCO.NS", "BPCL.NS", "SHRIRAMFIN.NS",
]


def get_universe() -> list[str]:
    return NIFTY50
