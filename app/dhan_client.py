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
# Dhan's marketfeed docs cap these endpoints at 1 request/second. Streamlit
# reruns every tab's code on every rerun (not just the visible tab), so the
# Overview, Scanner, and Watchlist tabs' quote calls all fire within the same
# script execution — comfortably faster than 1/sec without this throttle.
MIN_CALL_INTERVAL_SECONDS = 1.1


class DhanTokenExpiredError(RuntimeError):
    pass


class DhanApiError(RuntimeError):
    """
    Dhan's request succeeded at the transport level but the API itself
    reported a failure (bad security ID, rate limit, malformed request, ...).

    dhanhq's own HTTP layer (dhan_http.DhanHTTP._parse_response) wraps every
    call as {status, remarks, data}: on success `data` holds Dhan's raw
    response body verbatim, which for most endpoints is *itself* a second
    {status, data} envelope — so the real payload sits two levels deep, not
    one. On failure `data` is '' (an empty string, not {}) and the actual
    error lives in `remarks`. Naively doing `response["data"].get(...)`
    either silently returns nothing (success case, wrong nesting level) or
    crashes with "'str' object has no attribute 'get'" (failure case) —
    see _unwrap() below, which every method on DhanMarketClient goes through.
    """

    def __init__(self, message: str, http_status: int | None = None):
        super().__init__(message)
        self.http_status = http_status


def _unwrap(response: dict, what: str):
    """Normalize a raw dhanhq call to just its payload, or raise DhanApiError
    with Dhan's actual error message. See DhanApiError's docstring."""
    if not isinstance(response, dict) or response.get("status") != "success":
        remarks = response.get("remarks") if isinstance(response, dict) else None
        message = remarks.get("error_message") if isinstance(remarks, dict) else remarks
        status = remarks.get("http_status") if isinstance(remarks, dict) else None
        prefix = f"HTTP {status}: " if status else ""
        raise DhanApiError(f"{what} failed: {prefix}{message or response!r}", http_status=status)
    body = response.get("data")
    if isinstance(body, dict) and "data" in body and "status" in body:
        return body["data"]  # the common double-{status,data} envelope
    return body


def friendly_error(e: Exception) -> str:
    """
    User-facing message for a Dhan call failure, for display in st.error().

    A bare HTTP 401 with an empty Dhan error body (no errorCode/errorType/
    errorMessage) means client ID and access token were both accepted as
    well-formed but marketfeed access was refused — confirmed against this
    project's own Dhan account 2026-09-23: generating a trading access token
    does not by itself grant marketfeed/* (live quotes) access, that's a
    separate Data API subscription that has to be purchased/activated
    separately at web.dhan.co. If that gets activated and this still 401s,
    the cause is something else (wrong client ID, stale token) — see the
    plain str(e) fallback below and dhan_client's CLAUDE.md section.
    """
    if isinstance(e, DhanApiError) and e.http_status == 401:
        return (
            "Dhan Data API isn't active on this account — a trading access "
            "token alone doesn't grant live-quote access. Activate/purchase "
            "Data API access at web.dhan.co, then retry."
        )
    return str(e)


def _patch_http_diagnostics(dhan_http) -> None:
    """
    dhanhq's own _parse_response() only extracts errorCode/errorType/
    errorMessage from the response body — on a gateway-level rejection
    (rate limit, auth) whose body isn't shaped like Dhan's own app-level
    error JSON, all three come back None and the real HTTP status code
    (the one signal that tells a 429 from a 401) is discarded. Wrap it once
    per client to keep that status code in `remarks` for _unwrap() to show.
    """
    original = dhan_http._parse_response

    def _parse_response_with_status(response, *args, **kwargs):
        result = original(response, *args, **kwargs)
        if result.get("status") != "success" and isinstance(result.get("remarks"), dict):
            result["remarks"]["http_status"] = response.status_code
        return result

    dhan_http._parse_response = _parse_response_with_status


def _coerce_securities(securities: dict) -> dict:
    """dhanhq's own securities examples use integer security IDs; this repo
    carries them as strings everywhere else (they're DataFrame values and
    dict keys), so normalize at the API boundary instead of chasing
    str-vs-int through every caller."""
    return {segment: [int(s) for s in ids] for segment, ids in securities.items()}


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
        # docker-compose's env_file parser doesn't strip quotes the way a
        # shell does — DHAN_CLIENT_ID="123" in .env becomes the literal
        # string '"123"' (quotes included), which silently breaks auth as a
        # 401 with no useful error body. Normalize defensively, same as
        # set_token() already does for the pasted access token below.
        raw_client_id = client_id or os.environ.get("DHAN_CLIENT_ID") or ""
        self.client_id = raw_client_id.strip().strip("'\"")
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
        _patch_http_diagnostics(self._dhan.dhan_http)
        self._last_call_at = 0.0

    @property
    def token_hours_remaining(self) -> float:
        return self._token_state.hours_remaining

    def _throttle(self) -> None:
        """Keep consecutive Dhan calls >= MIN_CALL_INTERVAL_SECONDS apart —
        see the constant's comment for why this matters within one rerun."""
        elapsed = time.monotonic() - self._last_call_at
        if elapsed < MIN_CALL_INTERVAL_SECONDS:
            time.sleep(MIN_CALL_INTERVAL_SECONDS - elapsed)
        self._last_call_at = time.monotonic()

    def get_ltp(self, securities: dict) -> dict:
        """
        securities: {"NSE_EQ": [security_id, ...], "NSE_FNO": [...], "BSE_EQ": [...]}
        Returns {"NSE_EQ": {security_id: {...}, ...}, ...} for up to 1000
        instruments per call — already unwrapped, see DhanApiError.
        """
        self._throttle()
        securities = _coerce_securities(securities)
        raw = self._dhan.ohlc_data(securities=securities) if hasattr(self._dhan, "ohlc_data") \
            else self._dhan.quote_data(securities=securities)
        return _unwrap(raw, "get_ltp")

    def get_quote(self, securities: dict) -> dict:
        """Full quote incl. OI, depth — same securities dict shape as get_ltp."""
        self._throttle()
        raw = self._dhan.quote_data(securities=_coerce_securities(securities))
        return _unwrap(raw, "get_quote")

    def get_option_chain(self, under_security_id: str, under_exchange_segment: str, expiry: str) -> dict:
        """
        F&O option chain: OI, greeks, volume, top bid/ask per strike.
        expiry format: "YYYY-MM-DD" — call get_expiry_list first if unknown.
        """
        self._throttle()
        raw = self._dhan.option_chain(
            under_security_id=under_security_id,
            under_exchange_segment=under_exchange_segment,
            expiry=expiry,
        )
        return _unwrap(raw, "get_option_chain")

    def get_expiry_list(self, under_security_id: str, under_exchange_segment: str) -> list:
        self._throttle()
        raw = self._dhan.expiry_list(
            under_security_id=under_security_id,
            under_exchange_segment=under_exchange_segment,
        )
        return _unwrap(raw, "get_expiry_list")

    def get_fund_limits(self) -> dict:
        """Read-only — balance/margin info, no trading action."""
        self._throttle()
        return _unwrap(self._dhan.get_fund_limits(), "get_fund_limits")

    def get_intraday_minute_data(self, security_id: str, exchange_segment: str, instrument_type: str = "EQUITY"):
        """
        Minute candles for today — powers the Trend sparkline column. Method
        name follows dhanhq's documented historical-data API; wrapped in a
        try/except at the call site since exact kwargs have shifted across
        dhanhq releases — confirm against your installed version if it errors.
        """
        self._throttle()
        raw = self._dhan.intraday_minute_data(
            security_id=security_id,
            exchange_segment=exchange_segment,
            instrument_type=instrument_type,
        )
        return _unwrap(raw, "get_intraday_minute_data")
