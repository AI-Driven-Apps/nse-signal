"""
Single-page trading dashboard: market status/breadth, live equity + F&O
scanner, option chain, and signals from app.strategies.STRATEGIES.

Run (from repo root): python -m streamlit run app/dashboard.py \
    --server.address 0.0.0.0 --server.port 8501
(the docker-compose `dashboard` service runs exactly this)

Display-only — no orders placed from here.
"""

import sys
import time
from datetime import datetime
from pathlib import Path

import streamlit as st
import pandas as pd
from streamlit_autorefresh import st_autorefresh

from dhan_client import DhanMarketClient, DhanTokenExpiredError, set_token
from market_status import get_market_status, get_index_snapshot
from instrument_master import (
    load_scrip_master, get_nse_equity_universe, get_fno_underlyings, INDEX_UNDERLYINGS,
)
from scanner import compute_breadth, quotes_to_dataframe
from components import format_inr_compact, range_bar_html, oi_range_bar_html, strength_gauge_html

# dashboard.py lives at app/dashboard.py and is run as a standalone script
# (its sibling imports above rely on Python adding this file's own directory
# to sys.path), so reaching the `app` package for the signal registry and DB
# needs the repo root added explicitly rather than assumed.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.db import session_scope  # noqa: E402
from app.strategies import STRATEGIES  # noqa: E402
from app.web.data import recent_signals  # noqa: E402

st.set_page_config(page_title="Market Dashboard", layout="wide")

# --- Sidebar: token + refresh controls ---
st.sidebar.header("Connection")
new_token = st.sidebar.text_input("Paste fresh Dhan access token", type="password")
if st.sidebar.button("Save token"):
    if new_token.strip():
        state = set_token(new_token)
        st.sidebar.success(f"Valid until {state.expires_at:%Y-%m-%d %H:%M} UTC")
    else:
        st.sidebar.warning("Paste a token first.")

st.sidebar.divider()
poll_seconds = st.sidebar.slider("Auto-refresh (seconds)", 5, 60, 15)
auto_refresh = st.sidebar.checkbox("Auto", value=True)
universe_choice = st.sidebar.radio("Scanner universe", ["F&O stocks (~210)", "Custom watchlist"])

if auto_refresh:
    st_autorefresh(interval=poll_seconds * 1000, key="refresh_tick")

st.title("Market Dashboard")

# --- Market status strip ---
status = get_market_status()
c1, c2, c3 = st.columns(3)
c1.metric("NSE", status["nse"])
c2.metric("BSE", status["bse"])
c3.metric("As of", datetime.now().strftime("%H:%M:%S") + " IST")

try:
    client = DhanMarketClient()
    st.caption(f"Dhan token valid for another {client.token_hours_remaining:.1f}h")
except DhanTokenExpiredError as e:
    st.error(str(e))
    st.info("Generate a new token at web.dhan.co -> My Profile -> Access DhanHQ APIs, "
             "then paste it in the sidebar.")
    st.stop()

st.divider()

tab_overview, tab_scanner, tab_options, tab_watchlist, tab_signals = st.tabs(
    ["Overview", "Full Scanner", "Option Chain", "Watchlist", "Signals"]
)

# --- Cache the instrument universe (expensive, changes once a day) ---
@st.cache_data(ttl=3600 * 6)
def _universe():
    scrip_df = load_scrip_master()
    eq = get_nse_equity_universe(scrip_df)
    fno_symbols = set(get_fno_underlyings(scrip_df))
    eq["is_fno"] = eq["SEM_TRADING_SYMBOL"].isin(fno_symbols)
    return eq

try:
    universe_df = _universe()
except Exception as e:
    st.error(f"Couldn't load Dhan scrip master: {e}")
    st.stop()

if universe_choice.startswith("F&O"):
    active_universe = universe_df[universe_df["is_fno"]]
else:
    watchlist_ids = st.sidebar.text_area(
        "Watchlist (Dhan security IDs, comma-separated)", value="1333,11536"
    ).split(",")
    active_universe = universe_df[universe_df["SEM_SMST_SECURITY_ID"].astype(str).isin(
        [s.strip() for s in watchlist_ids]
    )]

security_ids = active_universe["SEM_SMST_SECURITY_ID"].astype(str).tolist()

# --- Overview tab: breadth + strength + index snapshot ---
with tab_overview:
    index_snap = get_index_snapshot()
    ic1, ic2 = st.columns(2)
    ic1.write("**Index snapshot**")
    ic1.json(index_snap)

    if security_ids:
        try:
            breadth = compute_breadth(client, security_ids)
            ic2.write("**Market breadth**")
            b1, b2, b3 = ic2.columns(3)
            b1.metric("Advances", breadth.advances)
            b2.metric("Declines", breadth.declines)
            b3.metric("Unchanged", breadth.unchanged)
            st.markdown(strength_gauge_html(breadth.strength, breadth.label), unsafe_allow_html=True)
            nh, nl = st.columns(2)
            nh.metric("Near day high", breadth.near_high)
            nl.metric("Near day low", breadth.near_low)
            st.caption(f"Universe: {breadth.total} scrips scanned")
        except Exception as e:
            st.error(f"Market breadth unavailable: {e}")
    else:
        st.info("No securities in the selected universe.")

# --- Full Scanner tab: day-range + OI-range grid, like the reel ---
with tab_scanner:
    show_n = st.selectbox("Show", [50, 100, 250, 500], index=1)
    sort_by = st.selectbox("Sort by", ["pct_change", "ltp", "oi"], index=0)

    if security_ids:
        try:
            df = quotes_to_dataframe(client, security_ids)
            df = df.merge(
                active_universe[["SEM_SMST_SECURITY_ID", "SEM_TRADING_SYMBOL"]]
                .rename(columns={"SEM_SMST_SECURITY_ID": "security_id", "SEM_TRADING_SYMBOL": "symbol"})
                .assign(security_id=lambda d: d["security_id"].astype(str)),
                on="security_id", how="left",
            )
            df = df.sort_values(sort_by, ascending=False, na_position="last").head(show_n)
            max_oi_abs = df["oi"].abs().max() or 1

            st.write(f"**{len(df)} of {len(security_ids)} scrips**")
            for _, row in df.iterrows():
                cols = st.columns([2, 1, 2, 2])
                cols[0].write(f"**{row['symbol']}**  {row['ltp'] if pd.notna(row['ltp']) else '-'}")
                pct = row["pct_change"]
                if pd.notna(pct):
                    color = "#2ecc71" if pct >= 0 else "#e74c3c"
                    cols[1].markdown(f"<span style='color:{color}'>{pct:+.2f}%</span>", unsafe_allow_html=True)
                else:
                    cols[1].write("-")
                cols[2].markdown(range_bar_html(row["ltp"], row["low"], row["high"]), unsafe_allow_html=True)
                cols[3].markdown(oi_range_bar_html(row["oi"], max_oi_abs), unsafe_allow_html=True)
        except Exception as e:
            st.error(f"Scanner unavailable: {e}")
    else:
        st.info("No securities in the selected universe.")

# --- Option Chain tab ---
with tab_options:
    underlying = st.selectbox("Underlying", list(INDEX_UNDERLYINGS.keys()))
    info = INDEX_UNDERLYINGS[underlying]
    try:
        expiries = client.get_expiry_list(info["security_id"], info["segment"])
        expiry = st.selectbox("Expiry", expiries)
        if expiry:
            chain = client.get_option_chain(info["security_id"], info["segment"], expiry)
            spot = chain.get("last_price")
            st.metric(f"{underlying} spot", spot)

            oc = chain.get("oc", {})
            rows = []
            if oc:
                nearest_strike_diff = min(abs(float(s) - spot) for s in oc.keys()) if spot else None
            for strike, legs in oc.items():
                strike_f = float(strike)
                is_atm = spot and nearest_strike_diff is not None and abs(strike_f - spot) == nearest_strike_diff
                for opt_type in ("ce", "pe"):
                    leg = legs.get(opt_type, {})
                    if leg:
                        rows.append({
                            "strike": strike_f,
                            "type": opt_type.upper(),
                            "ATM": "ATM" if is_atm else "",
                            "ltp": leg.get("last_price"),
                            "oi": leg.get("oi"),
                            "iv": leg.get("implied_volatility"),
                            "volume": leg.get("volume"),
                        })
            if rows:
                chain_df = pd.DataFrame(rows).sort_values(["strike", "type"])
                st.dataframe(chain_df, use_container_width=True, hide_index=True)
    except Exception as e:
        st.warning(f"Option chain unavailable: {e}")

# --- Watchlist tab (raw quotes, original simple view) ---
with tab_watchlist:
    raw_ids = st.text_area(
        "Security IDs (comma-separated)", value="1333,11536", key="watchlist_raw"
    ).split(",")
    ids = [s.strip() for s in raw_ids if s.strip()]
    if ids:
        try:
            st.json(client.get_quote({"NSE_EQ": ids}))
        except Exception as e:
            st.error(f"Watchlist quote unavailable: {e}")

# --- Signals tab ---
with tab_signals:
    st.subheader("Recent signals (manual execution)")
    st.caption(
        f"Registered strategies: {', '.join(STRATEGIES)}. Rows below are what the "
        "nightly signal engine already computed via Strategy.positions() (see /signals "
        "on the main site) — this tab reads that, it never recomputes positions itself."
    )
    with session_scope() as s:
        signal_rows = recent_signals(s, limit=25)
    if signal_rows:
        st.dataframe(pd.DataFrame(signal_rows), use_container_width=True, hide_index=True)
    else:
        st.write("No signals computed yet.")
