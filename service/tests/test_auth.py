"""CMS-linked login (app/auth.py), against a fake CMS; no network."""

import base64
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth import AuthConfig, SessionAuth, local_path, read_auth_config

APP = "http://localhost:5173"
CMS = "http://localhost:8102"
SECRET = "s" * 40
CONFIG = AuthConfig(app=APP, cms=CMS, api=CMS + "/api", client_id="cold-email", secret=SECRET, secure=False)
CODE = "c" * 43


class FakeCms:
    def __init__(self):
        self.role, self.status, self.online = "admin", 200, True
        self.calls: list[tuple[str, dict]] = []
        self.challenge = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if not self.online:
            raise httpx.ConnectError("offline")
        assert request.headers["authorization"] == f"Bearer {SECRET}"
        path, body = request.url.path.rsplit("/", 1)[1], json.loads(request.content)
        assert body["client_id"] == "cold-email"
        self.calls.append((path, body))
        if path == "exchange":
            verifier = body["code_verifier"]
            assert base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=") == self.challenge
            return httpx.Response(200, json={"user": {"id": "user-1", "email": "a@b.test", "role": self.role}, "issued_at": 1234.5})
        if path == "role":
            assert body["issued_at"] == 1234.5
            return httpx.Response(self.status, json={"role": self.role})
        if path == "logout-ticket":
            return httpx.Response(200, json={"ticket": "t" * 43})
        return httpx.Response(404)

    def count(self, path):
        return sum(1 for p, _ in self.calls if p == path)


@pytest.fixture
def env():
    clock = {"t": 1000.0}
    cms = FakeCms()
    auth = SessionAuth(CONFIG, transport=httpx.MockTransport(cms), now=lambda: clock["t"], api_key="k" * 40)
    app = FastAPI()
    app.include_router(auth.router, prefix="/auth")

    @app.api_route("/stats", methods=["GET", "POST"])
    async def stats(user: dict = Depends(auth.require_session)):
        return {"user": user}

    with TestClient(app, follow_redirects=False) as client:
        def login(expect=303):
            client.cookies.clear()
            r = client.get("/auth/start", params={"next": "/#replies"})
            location = urlsplit(r.headers["location"])
            query = parse_qs(location.query)
            assert f"{location.scheme}://{location.netloc}{location.path}" == CMS + "/authorize"
            assert query["redirect_uri"] == [APP + "/api/auth/callback"]
            cms.challenge = query["code_challenge"][0]
            tx = r.cookies["cold_email_transaction_dev"]
            r = client.get("/auth/callback", params={"code": CODE, "state": query["state"][0]},
                           cookies={"cold_email_transaction_dev": tx})
            assert r.status_code == expect, r.text
            if expect != 303:
                return r
            assert r.headers["location"] == "/#replies"
            assert "httponly" in r.headers["set-cookie"].lower()
            cookie = {"cold_email_session_dev": r.cookies["cold_email_session_dev"]}
            client.cookies.clear()
            csrf = client.get("/auth/me", cookies=cookie).json()["csrfToken"]
            return cookie, {"Origin": APP, "X-CSRF-Token": csrf}

        def advance(seconds):
            clock["t"] += seconds

        yield client, cms, login, advance


def test_login_session_and_csrf(env):
    client, cms, login, _ = env
    assert client.get("/stats").status_code == 401
    cookie, headers = login()
    assert client.get("/stats", cookies=cookie).json()["user"]["email"] == "a@b.test"
    assert client.post("/stats", cookies=cookie).status_code == 403
    assert client.post("/stats", cookies=cookie, headers={**headers, "Origin": "https://evil.test"}).status_code == 403
    assert client.post("/stats", cookies=cookie, headers={**headers, "X-CSRF-Token": "wrong"}).status_code == 403
    assert client.post("/stats", cookies=cookie, headers=headers).status_code == 200


def test_callback_needs_the_same_browser_and_state(env):
    client, cms, _, _ = env
    r = client.get("/auth/start")
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    tx = r.cookies["cold_email_transaction_dev"]
    client.cookies.clear()
    assert client.get("/auth/callback", params={"code": CODE, "state": state}).status_code == 400
    assert client.get("/auth/callback", params={"code": CODE, "state": "x" * 43},
                      cookies={"cold_email_transaction_dev": tx}).status_code == 400
    # The transaction is single-use even after a failed attempt.
    assert client.get("/auth/callback", params={"code": CODE, "state": state},
                      cookies={"cold_email_transaction_dev": tx}).status_code == 400
    assert cms.count("exchange") == 0


def test_disallowed_role_cannot_sign_in(env):
    client, cms, login, _ = env
    cms.role = "intern"
    r = login(expect=403)
    assert "cold_email_session_dev" not in r.cookies


def test_cms_signout_and_role_removal_end_the_session(env):
    client, cms, login, advance = env
    cookie, _ = login()
    cms.status = 401  # user signed out of CMS after this login
    assert client.get("/auth/me", cookies=cookie).status_code == 200  # cached for role_ttl
    advance(61)
    assert client.get("/stats", cookies=cookie).status_code == 401
    cms.status = 200
    assert client.get("/stats", cookies=cookie).status_code == 401  # session was deleted
    cookie, _ = login()
    cms.status = 403
    advance(61)
    assert client.get("/stats", cookies=cookie).status_code == 403
    cms.status = 200
    assert client.get("/stats", cookies=cookie).status_code == 401


def test_cms_outage_fails_closed_without_signing_out(env):
    client, cms, login, advance = env
    cookie, _ = login()
    cms.online = False
    advance(61)
    assert client.get("/stats", cookies=cookie).status_code == 503
    cms.online = True
    assert client.get("/stats", cookies=cookie).status_code == 200


def test_focus_recheck_is_immediate_but_throttled(env):
    client, cms, login, advance = env
    cookie, _ = login()
    for _ in range(3):
        assert client.get("/auth/me", params={"fresh": "1"}, cookies=cookie).status_code == 200
    assert cms.count("role") == 0
    advance(6)
    cms.status = 401
    assert client.get("/auth/me", params={"fresh": "1"}, cookies=cookie).status_code == 401
    assert cms.count("role") == 1


def test_idle_and_absolute_expiry(env):
    client, _, login, advance = env
    cookie, headers = login()
    advance(1500)
    assert client.post("/auth/activity", cookies=cookie, headers=headers).status_code == 204
    advance(1500)  # 3000s since login, but only 1500s idle
    assert client.get("/stats", cookies=cookie).status_code == 200
    advance(1801)
    assert client.get("/stats", cookies=cookie).status_code == 401
    cookie, headers = login()
    for _ in range(20):
        advance(1500)
        client.post("/auth/activity", cookies=cookie, headers=headers)
    assert client.get("/stats", cookies=cookie).status_code == 401  # past the 8h maximum


def test_logout_ends_session_and_hands_off_to_cms(env):
    client, cms, login, _ = env
    cookie, headers = login()
    assert client.post("/auth/logout", cookies=cookie, headers={**headers, "Origin": "https://evil.test"}).status_code == 403
    assert client.post("/auth/logout", cookies=cookie, headers={**headers, "X-CSRF-Token": "wrong"}).status_code == 403
    r = client.post("/auth/logout", cookies=cookie, headers=headers)
    assert r.json()["redirect"] == f"{CMS}/logout?client_id=cold-email&ticket={'t' * 43}"
    assert client.get("/stats", cookies=cookie).status_code == 401
    # CMS down: still signed out here, CMS will ask before signing out.
    cookie, headers = login()
    cms.online = False
    r = client.post("/auth/logout", cookies=cookie, headers=headers)
    assert r.json()["redirect"] == f"{CMS}/logout?client_id=cold-email"
    assert client.get("/stats", cookies=cookie).status_code == 401


def test_api_key_is_for_scripts_only(env):
    client, *_ = env
    assert client.get("/stats", headers={"Authorization": "Bearer " + "k" * 40}).status_code == 200
    assert client.post("/stats", headers={"Authorization": "Bearer " + "k" * 40}).status_code == 200
    assert client.get("/stats", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_local_path_blocks_open_redirects():
    for bad in ["https://evil.test", "//evil.test", "/\\evil", "/api/auth/start", "/a b", None, "x"]:
        assert local_path(bad) == "/"
    assert local_path("/#replies") == "/#replies"


def base_env(**extra):
    return {"APP_PUBLIC_URL": APP, "CMS_PUBLIC_URL": CMS, "SSO_CLIENT_SECRET": SECRET, **extra}


def test_config_defaults_and_docker_rewrite():
    c = read_auth_config(base_env(), in_docker=False)
    assert (c.api, c.client_id, c.secure, c.role_ttl) == (CMS + "/api", "cold-email", False, 60)
    assert read_auth_config(base_env(), in_docker=True).api == "http://host.docker.internal:8102/api"
    assert read_auth_config(base_env(CMS_API_BASE_URL="http://localhost:9000"), in_docker=True).api == "http://localhost:9000"


@pytest.mark.parametrize("change", [
    {"SSO_CLIENT_SECRET": "short"},
    {"APP_PUBLIC_URL": CMS},
    {"APP_PUBLIC_URL": "http://console.example.com"},
    {"APP_PUBLIC_URL": APP + "/path"},
    {"NODE_ENV": "production"},
    {"CMS_API_BASE_URL": "http://cms-internal:8100"},
    {"AUTH_SESSION_TTL_SECONDS": "999999"},
    {"APP_PUBLIC_URL": ""},
])
def test_unsafe_config_is_rejected(change):
    with pytest.raises(ValueError):
        read_auth_config(base_env(**change), in_docker=False)
