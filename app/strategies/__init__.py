"""Strategy registry. The dashboard switcher (Phase 2) just writes one of
these keys to settings.active_strategy — nothing else changes."""
from .base import Strategy
from .rsi_reversion import RsiReversion
from .sma_cross import SmaCross

STRATEGIES: dict[str, type[Strategy]] = {
    SmaCross.name: SmaCross,
    RsiReversion.name: RsiReversion,
}


def get_strategy(name: str) -> Strategy:
    if name not in STRATEGIES:
        raise KeyError(f"Unknown strategy '{name}'. Available: {list(STRATEGIES)}")
    return STRATEGIES[name]()
