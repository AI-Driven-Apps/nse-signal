import os

# Swap to Supabase in Phase 3 by changing only this env var, e.g.
# postgresql+psycopg2://user:pass@db.xxx.supabase.co:5432/postgres
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////data/backstage.db")

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# Default strategy on first boot; afterwards read from settings table
DEFAULT_STRATEGY = os.getenv("DEFAULT_STRATEGY", "sma_cross")

# How much history to keep/backfill on first ingest
BACKFILL_DAYS = int(os.getenv("BACKFILL_DAYS", "730"))

# Minimum bars a strategy needs before emitting signals
MIN_BARS = int(os.getenv("MIN_BARS", "120"))

TZ = os.getenv("TZ", "Asia/Kolkata")
