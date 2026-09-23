"""
Read-only Dhan API client for market monitoring.

Deliberately excludes order placement / modification — this dashboard is for
generating signals only; execution stays manual, per design.

Dhan individual-trader access tokens expire every 24 hours (generated from
web.dhan.co > My Profile > Access DhanHQ APIs). This module tracks token age
and warns/fails loudly rather than silently serving stale data, since a dead
token still returns HTTP 200s with an auth-error payload rather than a clean
401 in some cases.
"""

import os
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass

from dhanhq import DhanContext, dhanhq

TOKEN_STATE_PATH = Path(os.environ.get("DHAN_TOKEN_STATE", ".dhan_token_state.json"))
TOKEN_LIFETIME_HOURS = 24


class DhanTokenExpiredError(RuntimeError):
    pass


@dataclass
class TokenState:
    access_token: str
    issued_at: datetime

    @property
    def expires_at(self) -> datetime:
        return self.issued_at + timedelta(hours=TOKEN_LIFETIME_HOURS)

    @property
    def is_expired(self) -> bool:
        return datetime.utcnow() >= self.expires_at

    @property
    def hours_remaining(self) -> float:
        return max(0.0, (self.expires_at - datetime.utcnow()).total_seconds() / 3600)


def _load_token_state() -> TokenState | None:
    if not TOKEN_STATE_PATH.exists():
        return None
    data = json.loads(TOKEN_STATE_PATH.read_text())
    return TokenState(
        access_token=data["access_token"],
        issued_at=datetime.fromisoformat(data["issued_at"]),
    )


def _save_token_state(access_token: str) -> TokenState:
    state = TokenState(access_token=access_token, issued_at=datetime.utcnow())
    TOKEN_STATE_PATH.write_text(json.dumps({
        "access_token": state.access_token,
        "issued_at": state.issued_at.isoformat(),
    }))
    return state


def set_token(access_token: str) -> TokenState:
    """
    Call this once whenever you paste a freshly generated token from
    web.dhan.co. Records the issue time so hours_remaining stays accurate.
    """
    return _save_token_state(access_token.strip())


class DhanMarketClient:
    """
    Thin wrapper around dhanhq for read-only market data. Raises
    DhanTokenExpiredError proactively rather than letting a stale token
    fail silently mid-dashboard-refresh.
    """

    def __init__(self, client_id: str | None = None):
        self.client_id = client_id or os.environ.get("DHAN_CLIENT_ID")
        if not self.client_id:
            raise ValueError("DHAN_CLIENT_ID not set (env var or constructor arg)")

        state = _load_token_state()
        if state is None:
            raise DhanTokenExpiredError(
                "No Dhan access token on record. Generate one at web.dhan.co "
                "(My Profile > Access DhanHQ APIs) and call set_token(...)."
            )
        if state.is_expired:
            raise DhanTokenExpiredError(
                f"Dhan access token expired at {state.expires_at.isoformat()}Z. "
                "Generate a fresh one at web.dhan.co and call set_token(...)."
            )

        self._token_state = state
        context = DhanContext(self.client_id, state.access_token)
        self._dhan = dhanhq(context)

    @property
    def token_hours_remaining(self) -> float:
        return self._token_state.hours_remaining

    def get_ltp(self, securities: dict) -> dict:
        """
        securities: {"NSE_EQ": [security_id, ...], "NSE_FNO": [...], "BSE_EQ": [...]}
        Returns Dhan's LTP payload for up to 1000 instruments per call.
        """
        return self._dhan.ohlc_data(securities=securities) if hasattr(self._dhan, "ohlc_data") \
            else self._dhan.quote_data(securities=securities)

    def get_quote(self, securities: dict) -> dict:
        """Full quote incl. OI, depth — same securities dict shape as get_ltp."""
        return self._dhan.quote_data(securities=securities)

    def get_option_chain(self, under_security_id: str, under_exchange_segment: str, expiry: str) -> dict:
        """
        F&O option chain: OI, greeks, volume, top bid/ask per strike.
        expiry format: "YYYY-MM-DD" — call get_expiry_list first if unknown.
        """
        return self._dhan.option_chain(
            under_security_id=under_security_id,
            under_exchange_segment=under_exchange_segment,
            expiry=expiry,
        )

    def get_expiry_list(self, under_security_id: str, under_exchange_segment: str) -> list:
        return self._dhan.expiry_list(
            under_security_id=under_security_id,
            under_exchange_segment=under_exchange_segment,
        )

    def get_fund_limits(self) -> dict:
        """Read-only — balance/margin info, no trading action."""
        return self._dhan.get_fund_limits()

    def get_intraday_minute_data(self, security_id: str, exchange_segment: str, instrument_type: str = "EQUITY"):
        """
        Minute candles for today — powers the Trend sparkline column. Method
        name follows dhanhq's documented historical-data API; wrapped in a
        try/except at the call site since exact kwargs have shifted across
        dhanhq releases — confirm against your installed version if it errors.
        """
        return self._dhan.intraday_minute_data(
            security_id=security_id,
            exchange_segment=exchange_segment,
            instrument_type=instrument_type,
        )
