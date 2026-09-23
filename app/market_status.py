"""
NSE/BSE market status + index snapshot for the Market Dashboard's status
strip. Rendered before the Dhan client is constructed (dashboard.py calls
this first), so it must stand on its own without a Dhan token.

NSE status comes from nsepython's live NSE status feed. NSE's API has no
BSE equivalent (and nsepython doesn't wrap a separate BSE source), so BSE
is approximated from the shared NSE/BSE cash-market window (09:15-15:30
IST, Mon-Fri) rather than a live feed. Good enough for an "open/closed"
strip; swap in a real BSE status source here if one gets added later.
"""

from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from nsepython import nse_get_index_quote, nse_marketStatus

IST = ZoneInfo("Asia/Kolkata")
CASH_MARKET_OPEN = dtime(9, 15)
CASH_MARKET_CLOSE = dtime(15, 30)

# Kept small — each is a separate live fetch against niftyindices' blob.
SNAPSHOT_INDICES = ["NIFTY 50", "NIFTY BANK", "INDIA VIX"]


def _nse_capital_market_status() -> str:
    try:
        payload = nse_marketStatus()
        for entry in payload.get("marketState", []):
            if entry.get("market") == "Capital Market":
                return entry.get("marketStatus", "Unknown")
    except Exception:
        pass
    return "Unknown"


def _bse_status_from_hours() -> str:
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return "Closed"
    return "Open" if CASH_MARKET_OPEN <= now.time() <= CASH_MARKET_CLOSE else "Closed"


def get_market_status() -> dict:
    return {
        "nse": _nse_capital_market_status(),
        "bse": _bse_status_from_hours(),
    }


def get_index_snapshot() -> dict:
    snapshot = {}
    for index in SNAPSHOT_INDICES:
        try:
            snapshot[index] = nse_get_index_quote(index)
        except Exception as e:
            snapshot[index] = {"error": str(e)}
    return snapshot
