"""Dashboard auth (§2.1) acceptance criterion: all routes behind auth;
a request without a session gets redirected to /login."""
from datetime import date

import app.web.app as web_app_mod
from fastapi.testclient import TestClient

from app.db import get_setting, session_scope
from app.models import Signal, Stock
from app.web.app import app
from app.web.auth import bootstrap_admin


def _authed_client() -> TestClient:
    with session_scope() as s:
        bootstrap_admin(s)
    client = TestClient(app, follow_redirects=False)
    client.post("/login", data={"username": "test_admin", "password": "test_password_123"})
    return client


def test_unauthenticated_redirects_to_login(db):
    client = TestClient(app, follow_redirects=False)
    r = client.get("/")
    assert r.status_code == 302
    assert r.headers["location"] == "/login"


def test_new_pages_require_auth(db):
    client = TestClient(app, follow_redirects=False)
    for path in ["/stats", "/strategy", "/watchlist", "/signals"]:
        r = client.get(path)
        assert r.status_code == 302 and r.headers["location"] == "/login", path


def test_new_pages_render_when_authed(db):
    client = _authed_client()
    for path in ["/stats", "/strategy", "/watchlist", "/signals"]:
        r = client.get(path)
        assert r.status_code == 200, path


def test_strategy_switch_and_backtest_trigger(db):
    client = _authed_client()
    r = client.post("/strategy/set", data={"name": "rsi_reversion"}, follow_redirects=False)
    assert r.status_code == 302
    with session_scope() as s:
        assert get_setting(s, "active_strategy") == "rsi_reversion"

    r = client.post("/strategy/backtest", data={"name": "rsi_reversion"}, follow_redirects=False)
    assert r.status_code == 302
    assert "message=" in r.headers["location"]


def test_watchlist_add_remove_via_routes(db):
    with session_scope() as s:
        s.add(Stock(symbol="AAA.NS"))
    client = _authed_client()

    r = client.post("/watchlist/add", data={"symbol": "aaa"}, follow_redirects=False)
    assert r.status_code == 302
    assert "error" not in r.headers["location"]

    page = client.get("/watchlist")
    assert "AAA" in page.text

    r = client.post("/watchlist/add", data={"symbol": "nope"}, follow_redirects=False)
    assert "error=" in r.headers["location"]


def test_signal_resend_calls_discord(db, monkeypatch):
    sent = []
    monkeypatch.setattr(web_app_mod, "send_signal", lambda *a, **k: sent.append(a) or True)

    with session_scope() as s:
        stock = Stock(symbol="AAA.NS")
        s.add(stock)
        s.flush()
        sig = Signal(stock_id=stock.id, date=date(2024, 6, 3), action="BUY",
                     price=100.0, strategy="sma_cross", sent_to_discord=False)
        s.add(sig)
        s.flush()
        signal_id = sig.id

    client = _authed_client()
    r = client.post(f"/signals/resend/{signal_id}", follow_redirects=False)
    assert r.status_code == 302
    assert len(sent) == 1

    with session_scope() as s:
        assert s.get(Signal, signal_id).sent_to_discord is True


def test_healthz_no_auth_required(db):
    client = TestClient(app, follow_redirects=False)
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_login_success_grants_overview_access(db):
    with session_scope() as s:
        bootstrap_admin(s)
    client = TestClient(app, follow_redirects=False)

    r = client.post("/login", data={"username": "test_admin", "password": "test_password_123"})
    assert r.status_code == 302
    assert r.headers["location"] == "/"
    assert "nse_signal_session" in client.cookies

    r2 = client.get("/")
    assert r2.status_code == 200
    assert "Overview" in r2.text


def test_login_wrong_password_rejected(db):
    with session_scope() as s:
        bootstrap_admin(s)
    client = TestClient(app, follow_redirects=False)

    r = client.post("/login", data={"username": "test_admin", "password": "wrong"})
    assert r.status_code == 401
    assert "nse_signal_session" not in client.cookies
