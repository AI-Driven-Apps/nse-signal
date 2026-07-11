"""Background backtest runner for the "run backtest now" button (§2.5).
A plain daemon thread — invariant 7 bans Celery/Redis on the Pi, and a
single-user dashboard has no need for a real task queue. One run at a time."""
import logging
import threading

log = logging.getLogger("web.tasks")

_lock = threading.Lock()
_running: str | None = None


def running_strategy() -> str | None:
    return _running


def start_backtest(strategy_name: str) -> bool:
    """Returns False (no-op) if a backtest is already in flight."""
    global _running
    with _lock:
        if _running is not None:
            return False
        _running = strategy_name

    def _run():
        global _running
        from ..backtest import backtest_universe
        try:
            backtest_universe(strategy_name)
        except Exception:
            log.exception("Background backtest failed for '%s'", strategy_name)
        finally:
            with _lock:
                _running = None

    threading.Thread(target=_run, daemon=True, name=f"backtest-{strategy_name}").start()
    return True
