import logging

import requests

from .config import DISCORD_WEBHOOK_URL

log = logging.getLogger("notify")

GREEN, RED = 0x2ECC71, 0xE74C3C


def send_signal(symbol: str, action: str, price: float, strategy: str, when) -> bool:
    if not DISCORD_WEBHOOK_URL:
        log.warning("DISCORD_WEBHOOK_URL not set; skipping notification")
        return False
    embed = {
        "title": f"{'🟢' if action == 'BUY' else '🔴'} {action}: {symbol.replace('.NS', '')}",
        "color": GREEN if action == "BUY" else RED,
        "fields": [
            {"name": "Price", "value": f"₹{price:,.2f}", "inline": True},
            {"name": "Strategy", "value": strategy, "inline": True},
            {"name": "Date", "value": str(when), "inline": True},
        ],
        "footer": {"text": "Backstage · paper trading · not financial advice"},
    }
    try:
        r = requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        log.error("Discord send failed: %s", e)
        return False
