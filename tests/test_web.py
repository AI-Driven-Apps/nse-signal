"""Dashboard auth (§2.1) acceptance criterion: all routes behind auth;
a request without a session gets redirected to /login."""
from fastapi.testclient import TestClient

from app.db import session_scope
from app.web.app import app
from app.web.auth import bootstrap_admin


def test_unauthenticated_redirects_to_login(db):
    client = TestClient(app, follow_redirects=False)
    r = client.get("/")
    assert r.status_code == 302
    assert r.headers["location"] == "/login"


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
    assert "backstage_session" in client.cookies

    r2 = client.get("/")
    assert r2.status_code == 200
    assert "Overview" in r2.text


def test_login_wrong_password_rejected(db):
    with session_scope() as s:
        bootstrap_admin(s)
    client = TestClient(app, follow_redirects=False)

    r = client.post("/login", data={"username": "test_admin", "password": "wrong"})
    assert r.status_code == 401
    assert "backstage_session" not in client.cookies
