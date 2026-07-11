"""Golden Master config. Deliberately separate from app/config.py — this is
an isolated subsystem with its own DB and (currently) no live data feed.
See docs/golden_master_spec.md."""
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////data/golden_master.db")

# Shared with the main nse-signal container — same webhook, distinct embed
# styling (see notify.py) rather than a separate Discord channel.
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

SYMBOL = os.getenv("GM_SYMBOL", "^NSEI")

# ---- Strategy parameters (defaults match the source Lipi/Pine script) ----
SL_POINTS = float(os.getenv("GM_SL_POINTS", "25"))
TARGET_POINTS = float(os.getenv("GM_TARGET_POINTS", "25"))
STRIKE_OFFSET = float(os.getenv("GM_STRIKE_OFFSET", "100"))
BLUE_BREAKOUT_BUFFER = float(os.getenv("GM_BLUE_BREAKOUT_BUFFER", "3"))

REJECTION_START_HOUR = int(os.getenv("GM_REJECTION_START_HOUR", "9"))
REJECTION_START_MINUTE = int(os.getenv("GM_REJECTION_START_MINUTE", "30"))
FALLBACK_START_HOUR = int(os.getenv("GM_FALLBACK_START_HOUR", "9"))
FALLBACK_START_MINUTE = int(os.getenv("GM_FALLBACK_START_MINUTE", "45"))

# NSE cash/derivatives session close — bracket trades still open at this
# time are squared off at that bar's close (no overnight options carry).
EOD_SQUARE_OFF_HOUR = int(os.getenv("GM_EOD_HOUR", "15"))
EOD_SQUARE_OFF_MINUTE = int(os.getenv("GM_EOD_MINUTE", "30"))

TZ = os.getenv("TZ", "Asia/Kolkata")
