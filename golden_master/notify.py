"""Discord notification — deliberately a small, separate copy of
app/notify.py's pattern rather than an import, since the two containers are
independently deployable (see docs/golden_master_spec.md). Same webhook,
distinct embed styling (purple, "Golden Master" title) so it reads as a
different strategy in the same channel, per the confirmed design."""
import logging

import requests

from .config import DISCORD_WEBHOOK_URL

log = logging.getLogger("golden_master.notify")

PURPLE = 0x9B59B6


def send_golden_master_signal(symbol: str, signal_type: str, direction: str,
                              entry_price: float, sl_price: float, target_price: float,
                              when) -> bool:
    if not DISCORD_WEBHOOK_URL:
        log.warning("DISCORD_WEBHOOK_URL not set; skipping notification")
        return False
    embed = {
        "title": f"🟣 GOLDEN MASTER {direction}: {symbol} ({signal_type})",
        "color": PURPLE,
        "fields": [
            {"name": "Entry", "value": f"{entry_price:,.2f}", "inline": True},
            {"name": "Stop loss", "value": f"{sl_price:,.2f}", "inline": True},
            {"name": "Target", "value": f"{target_price:,.2f}", "inline": True},
            {"name": "Time", "value": str(when), "inline": True},
        ],
        "footer": {"text": "golden-master · intraday paper trade · not financial advice"},
    }
    try:
        r = requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        log.error("Discord send failed: %s", e)
        return False
