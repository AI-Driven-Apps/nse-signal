"""Dashboard (§2.1/§2.2). Server-rendered FastAPI + Jinja2, same container as
the scheduler, one port (WEB_PORT). Single-user auth only for now; Phase 3
swaps this behind AUTH_MODE (see CLAUDE.md)."""
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from ..config import COOKIE_MAX_AGE_DAYS
from ..db import get_setting, init_db, session_scope, set_setting
from ..models import Signal, Stock, User
from ..notify import send_signal
from ..strategies import STRATEGIES
from . import tasks
from .auth import (
    COOKIE_NAME, bootstrap_admin, client_ip, is_rate_limited,
    make_session_cookie, read_session_cookie, record_login_attempt,
    verify_password,
)
from .data import (
    add_watchlist_item, equity_curve, known_symbols, latest_mood,
    latest_snapshot, period_stats, recent_signals, remove_watchlist_item,
    strategy_metrics, suggested_stocks, toggle_star, top_movers,
    watchlist_rows,
)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


class NotAuthenticated(Exception):
    pass


def get_current_user(request: Request) -> User:
    token = request.cookies.get(COOKIE_NAME)
    uid = read_session_cookie(token) if token else None
    if uid is None:
        raise NotAuthenticated()
    with session_scope() as s:
        user = s.get(User, uid)
    if user is None:
        raise NotAuthenticated()
    return user


def create_app() -> FastAPI:
    init_db()
    with session_scope() as s:
        bootstrap_admin(s)

    app = FastAPI(title="nse-signal")

    @app.exception_handler(NotAuthenticated)
    def _redirect_to_login(request: Request, exc: NotAuthenticated):
        return RedirectResponse(url="/login", status_code=302)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/login")
    def login_form(request: Request):
        return templates.TemplateResponse(request, "login.html", {"user": None})

    @app.post("/login")
    def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
        ip = client_ip(request)
        if is_rate_limited(ip):
            return templates.TemplateResponse(
                request, "login.html",
                {"user": None, "error": "Too many attempts. Try again in a few minutes."},
                status_code=429)

        with session_scope() as s:
            user = s.execute(
                select(User).where(User.username == username)
            ).scalar_one_or_none()
            valid = user is not None and verify_password(password, user.password_hash)
            user_id = user.id if user else None

        if not valid:
            record_login_attempt(ip)
            return templates.TemplateResponse(
                request, "login.html",
                {"user": None, "error": "Invalid username or password."},
                status_code=401)

        resp = RedirectResponse(url="/", status_code=302)
        resp.set_cookie(COOKIE_NAME, make_session_cookie(user_id),
                        httponly=True, samesite="lax",
                        max_age=COOKIE_MAX_AGE_DAYS * 86400)
        return resp

    @app.get("/logout")
    def logout():
        resp = RedirectResponse(url="/login", status_code=302)
        resp.delete_cookie(COOKIE_NAME)
        return resp

    @app.get("/")
    def overview(request: Request, user: User = Depends(get_current_user)):
        with session_scope() as s:
            snapshot = latest_snapshot(s)
            mood = latest_mood(s)
            gainers, losers = top_movers(s)
            curve = equity_curve(s)
        dates = [d for d, _ in curve]
        values = [v for _, v in curve]
        return templates.TemplateResponse(request, "overview.html", {
            "user": user, "snapshot": snapshot, "mood": mood,
            "gainers": gainers, "losers": losers,
            "equity_dates": dates, "equity_values": values,
        })

    @app.get("/stats")
    def stats(request: Request, user: User = Depends(get_current_user), period: str = "weekly"):
        if period not in {"daily", "weekly", "monthly", "quarterly", "yearly"}:
            period = "weekly"
        with session_scope() as s:
            rows = period_stats(s, period)
        return templates.TemplateResponse(request, "stats.html", {
            "user": user, "period": period, "rows": rows,
        })

    @app.get("/strategy")
    def strategy_page(request: Request, user: User = Depends(get_current_user), message: str | None = None):
        with session_scope() as s:
            active = get_setting(s, "active_strategy", "sma_cross")
            metrics = strategy_metrics(s)
        return templates.TemplateResponse(request, "strategy.html", {
            "user": user, "strategies": list(STRATEGIES), "metrics": metrics,
            "active_strategy": active, "running": tasks.running_strategy(),
            "message": message,
        })

    @app.post("/strategy/set")
    def strategy_set(request: Request, user: User = Depends(get_current_user), name: str = Form(...)):
        if name not in STRATEGIES:
            return RedirectResponse(url="/strategy", status_code=302)
        with session_scope() as s:
            set_setting(s, "active_strategy", name)
        return RedirectResponse(url=f"/strategy?message=Active+strategy+set+to+{name}", status_code=302)

    @app.post("/strategy/backtest")
    def strategy_backtest(request: Request, user: User = Depends(get_current_user), name: str = Form(...)):
        if name in STRATEGIES:
            started = tasks.start_backtest(name)
            msg = f"Backtest+started+for+{name}" if started else "A+backtest+is+already+running"
            return RedirectResponse(url=f"/strategy?message={msg}", status_code=302)
        return RedirectResponse(url="/strategy", status_code=302)

    @app.get("/watchlist")
    def watchlist_page(request: Request, user: User = Depends(get_current_user), error: str | None = None):
        with session_scope() as s:
            active = get_setting(s, "active_strategy", "sma_cross")
            watchlist = watchlist_rows(s, active)
            suggestions = suggested_stocks(s, active)
            symbols = known_symbols(s)
        return templates.TemplateResponse(request, "watchlist.html", {
            "user": user, "watchlist": watchlist, "suggestions": suggestions,
            "known_symbols": symbols, "error": error,
        })

    @app.post("/watchlist/add")
    def watchlist_add(request: Request, user: User = Depends(get_current_user), symbol: str = Form(...)):
        symbol = symbol.strip().upper()
        if symbol and not symbol.endswith(".NS"):
            symbol += ".NS"
        with session_scope() as s:
            item = add_watchlist_item(s, symbol)
        if item is None:
            return RedirectResponse(url=f"/watchlist?error=Unknown+symbol+{symbol}", status_code=302)
        return RedirectResponse(url="/watchlist", status_code=302)

    @app.post("/watchlist/remove/{item_id}")
    def watchlist_remove(request: Request, item_id: int, user: User = Depends(get_current_user)):
        with session_scope() as s:
            remove_watchlist_item(s, item_id)
        return RedirectResponse(url="/watchlist", status_code=302)

    @app.post("/watchlist/star/{item_id}")
    def watchlist_star(request: Request, item_id: int, user: User = Depends(get_current_user)):
        with session_scope() as s:
            toggle_star(s, item_id)
        return RedirectResponse(url="/watchlist", status_code=302)

    @app.get("/signals")
    def signals_page(request: Request, user: User = Depends(get_current_user)):
        with session_scope() as s:
            signals = recent_signals(s)
        return templates.TemplateResponse(request, "signals.html", {
            "user": user, "signals": signals,
        })

    @app.post("/signals/resend/{signal_id}")
    def signals_resend(request: Request, signal_id: int, user: User = Depends(get_current_user)):
        with session_scope() as s:
            sig = s.get(Signal, signal_id)
            if sig is not None:
                stock = s.get(Stock, sig.stock_id)
                sig.sent_to_discord = send_signal(
                    stock.symbol, sig.action, sig.price, sig.strategy, sig.date)
        return RedirectResponse(url="/signals", status_code=302)

    return app


app = create_app()
