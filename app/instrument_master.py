"""
Dhan scrip master: the CSV of every tradable instrument (security_id, symbol,
lot size, expiry, strike, option type, exchange segment). Needed to build the
"All stocks / F&O / Non-F&O / ETFs" universe the scanner screens, and to tag
option-chain strikes as ATM/ITM/OTM against the lot size Dhan expects.

Refreshed once a day (Dhan republishes it each morning) and cached to disk —
no reason to re-download ~5MB of CSV on every dashboard rerun.
"""

import os
import time
from pathlib import Path

import pandas as pd
import requests

SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
# Same directory as the token state file, so the Docker volume covers both.
CACHE_PATH = Path(os.environ.get("DHAN_SCRIP_MASTER_CACHE", "scrip_master_cache.csv"))
CACHE_MAX_AGE_HOURS = 20  # refresh once daily, before market open


def _cache_is_stale() -> bool:
    if not CACHE_PATH.exists():
        return True
    age_hours = (time.time() - CACHE_PATH.stat().st_mtime) / 3600
    return age_hours > CACHE_MAX_AGE_HOURS


def load_scrip_master(force_refresh: bool = False) -> pd.DataFrame:
    if force_refresh or _cache_is_stale():
        resp = requests.get(SCRIP_MASTER_URL, timeout=30)
        resp.raise_for_status()
        CACHE_PATH.write_bytes(resp.content)
    return pd.read_csv(CACHE_PATH, low_memory=False)


def get_nse_equity_universe(df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Cash-market NSE equities only — the 'All stocks' / 'Non-F&O' base."""
    df = df if df is not None else load_scrip_master()
    return df[
        (df["SEM_EXM_EXCH_ID"] == "NSE")
        & (df["SEM_SEGMENT"] == "E")
        & (df["SEM_SERIES"].isin(["EQ", "BE"]))
    ].copy()


def get_fno_underlyings(df: pd.DataFrame | None = None) -> list[str]:
    """Symbols that have F&O contracts — matches the reel's 'F&O 210' count."""
    df = df if df is not None else load_scrip_master()
    fno = df[(df["SEM_EXM_EXCH_ID"] == "NSE") & (df["SEM_SEGMENT"] == "D")]
    return sorted(fno["SEM_TRADING_SYMBOL"].str.split(" ").str[0].unique().tolist())


def get_option_chain_instruments(
    underlying_symbol: str, expiry: str, df: pd.DataFrame | None = None
) -> pd.DataFrame:
    """
    All CE/PE strikes for one underlying + expiry, with security_id and lot
    size ready to feed into DhanMarketClient.get_option_chain / get_quote.
    """
    df = df if df is not None else load_scrip_master()
    mask = (
        (df["SEM_EXM_EXCH_ID"] == "NSE")
        & (df["SEM_SEGMENT"] == "D")
        & (df["SEM_TRADING_SYMBOL"].str.startswith(underlying_symbol))
        & (df["SEM_EXPIRY_DATE"] == expiry)
        & (df["SEM_OPTION_TYPE"].isin(["CE", "PE"]))
    )
    return df[mask][
        ["SEM_SMST_SECURITY_ID", "SEM_TRADING_SYMBOL", "SEM_OPTION_TYPE",
         "SEM_STRIKE_PRICE", "SEM_LOT_UNITS", "SEM_EXPIRY_DATE"]
    ].copy()


# Common index underlyings — security_id + segment, per Dhan's security master
# (these are stable identifiers Dhan documents, not looked up from the CSV)
INDEX_UNDERLYINGS = {
    "NIFTY 50": {"security_id": "13", "segment": "IDX_I"},
    "BANK NIFTY": {"security_id": "25", "segment": "IDX_I"},
    "FINNIFTY": {"security_id": "27", "segment": "IDX_I"},
    "MIDCPNIFTY": {"security_id": "442", "segment": "IDX_I"},
    "SENSEX": {"security_id": "51", "segment": "IDX_I"},
}
