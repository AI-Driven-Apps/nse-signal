import os

# Swap to Supabase in Phase 3 by changing only this env var, e.g.
# postgresql+psycopg2://user:pass@db.xxx.supabase.co:5432/postgres
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////data/nse_signal.db")

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# Default strategy on first boot; afterwards read from settings table
DEFAULT_STRATEGY = os.getenv("DEFAULT_STRATEGY", "sma_cross")

# How much history to keep/backfill on first ingest
BACKFILL_DAYS = int(os.getenv("BACKFILL_DAYS", "730"))

# Minimum bars a strategy needs before emitting signals
MIN_BARS = int(os.getenv("MIN_BARS", "120"))

# Emit signals for position flips on any bar within this many days of the
# latest bar. Wider than one day so a missed/failed nightly run is caught up
# on the next run instead of silently dropping the intervening transitions.
SIGNAL_LOOKBACK_DAYS = int(os.getenv("SIGNAL_LOOKBACK_DAYS", "7"))

# ---- Paper portfolio (Phase 2 §2.3) — see docs/phase2_specs.md ----
# Initial cash (₹), applied once when the first snapshot is written.
STARTING_CAPITAL = float(os.getenv("STARTING_CAPITAL", "1000000"))
# Fraction of current equity deployed on each BUY (% of equity sizing).
ALLOC_PCT = float(os.getenv("ALLOC_PCT", "0.10"))
# Cap on concurrent open positions (bounds ALLOC_PCT × MAX_POSITIONS).
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "10"))

# ---- India fear & greed (Phase 2 §2.2) — see docs/phase2_specs.md ----
NIFTY_SYMBOL = os.getenv("NIFTY_SYMBOL", "^NSEI")
VIX_SYMBOL = os.getenv("VIX_SYMBOL", "^INDIAVIX")
VIX_WINDOW = int(os.getenv("VIX_WINDOW", "90"))       # trailing sessions for VIX range
MOM_SMA = int(os.getenv("MOM_SMA", "125"))            # Nifty trend SMA period
MOM_BAND = float(os.getenv("MOM_BAND", "0.10"))       # +/-10% dev maps to 0/100
AD_WINDOW = int(os.getenv("AD_WINDOW", "5"))          # advance/decline lookback sessions
W_VIX = float(os.getenv("W_VIX", "1"))
W_MOM = float(os.getenv("W_MOM", "1"))
W_AD = float(os.getenv("W_AD", "1"))

# ---- Web dashboard (Phase 2 §2.1) ----
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
COOKIE_MAX_AGE_DAYS = int(os.getenv("COOKIE_MAX_AGE_DAYS", "7"))
WEB_PORT = int(os.getenv("WEB_PORT", "8000"))

TZ = os.getenv("TZ", "Asia/Kolkata")
