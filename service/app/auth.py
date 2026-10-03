"""CMS-linked login for the console.

Same protocol as the landing page (landing_page_automation/new_approach/
src/auth/session.mjs): the browser signs in at CMS, CMS hands back a 60-second
single-use code bound to this browser by S256 PKCE, and this service swaps it
server-to-server for the user's identity. The console then holds only an
opaque HttpOnly session cookie; no Supabase token or secret reaches it.

While a session lives, CMS is asked again every ``role_ttl`` whether the user
still has an allowed role and has not signed out of CMS since this login
(``issued_at``). Logout ends the session here, then sends the browser to CMS
``/logout`` with a single-use ticket so CMS (and every other app signed in
through it) is signed out too.

Sessions live in process memory: run one worker. A restart signs everyone out.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response

ROLES = {"admin", "marketing"}
LOOPBACK = {"localhost", "127.0.0.1", "::1", "[::1]"}
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
_NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


@dataclass(frozen=True)
class AuthConfig:
    app: str
    cms: str
    api: str
    client_id: str
    secret: str
    secure: bool
    transaction_ttl: int = 300
    session_ttl: int = 28800
    idle_ttl: int = 1800
    role_ttl: int = 60


def read_auth_config(env=os.environ, in_docker: Optional[bool] = None) -> AuthConfig:
    """Validate the login settings. Raises ValueError on anything unsafe."""
    if in_docker is None:
        in_docker = os.path.exists("/.dockerenv")
    # Only loopback HTTP is allowed, and only outside production.
    local = env.get("NODE_ENV") != "production" and env.get("AUTH_ALLOW_INSECURE_LOCALHOST") != "false"

    def origin(name: str) -> str:
        value = env.get(name, "").strip()
        u = urlsplit(value)
        if not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in ("", "/"):
            raise ValueError(f"{name} must be an origin such as https://console.example.com")
        # Plain HTTP is allowed on any host (e.g. a VPS without TLS); cookies
        # are marked Secure only when the console origin is HTTPS.
        if u.scheme not in ("http", "https"):
            raise ValueError(f"{name} must be an http:// or https:// origin")
        return f"{u.scheme}://{u.netloc}"

    def seconds(name: str, default: int) -> int:
        raw = env.get(name) or str(default)
        if not raw.isdigit() or not 1 <= int(raw) <= default:
            raise ValueError(f"{name} must be 1..{default}")
        return int(raw)

    app, cms = origin("APP_PUBLIC_URL"), origin("CMS_PUBLIC_URL")
    if app == cms:
        raise ValueError("APP_PUBLIC_URL and CMS_PUBLIC_URL must differ")
    explicit = env.get("CMS_API_BASE_URL", "").strip()
    api = urlsplit(explicit or f"{cms}/api")
    host = api.hostname or ""
    # Inside Docker, "localhost" is the container itself, not the host running
    # CMS. Rewrite only the derived development address, never an override.
    if not explicit and local and in_docker and host in LOOPBACK:
        api = api._replace(netloc=api.netloc.replace(host, "host.docker.internal", 1))
        host = "host.docker.internal"
    if api.scheme not in ("http", "https") or not host or api.username or api.password or api.query or api.fragment:
        raise ValueError("Invalid CMS_API_BASE_URL")
    client_id = env.get("SSO_CLIENT_ID") or "cold-email"
    secret = env.get("SSO_CLIENT_SECRET", "")
    if not re.fullmatch(r"[a-z0-9-]{1,64}", client_id) or not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", secret):
        raise ValueError("SSO_CLIENT_SECRET must be a URL-safe random value of 32..256 characters")
    return AuthConfig(
        app=app, cms=cms, api=urlunsplit(api).rstrip("/"), client_id=client_id, secret=secret,
        secure=app.startswith("https:"),
        transaction_ttl=seconds("AUTH_TRANSACTION_TTL_SECONDS", 300),
        session_ttl=seconds("AUTH_SESSION_TTL_SECONDS", 28800),
        idle_ttl=seconds("AUTH_IDLE_TIMEOUT_SECONDS", 1800),
        role_ttl=seconds("AUTH_ROLE_CACHE_TTL_SECONDS", 60),
    )


def local_path(value) -> str:
    """Only same-site paths are valid post-login destinations."""
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//") \
            or re.search(r"[\\\x00-\x20]", value) or len(value) > 2048 or value.startswith("/api/"):
        return "/"
    return value


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _equal(a, b) -> bool:
    return isinstance(a, str) and isinstance(b, str) and hmac.compare_digest(a.encode(), b.encode())


class AuthError(Exception):
    def __init__(self, status: int):
        self.status = status


@dataclass
class Session:
    user: dict
    issued_at: float
    csrf: str
    expires: float
    last_seen: float
    checked: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _error_body(status: int) -> dict:
    return {"detail": {401: "unauthorized", 403: "access_denied"}.get(status, "authorization_unavailable")}


class SessionAuth:
    def __init__(self, config: AuthConfig, *, transport: Optional[httpx.AsyncBaseTransport] = None,
                 now: Callable[[], float] = time.time, max_records: int = 5000,
                 api_key: str = ""):
        self.config = config
        self.now = now
        self.max_records = max_records
        # Optional machine credential (API_KEY) for scripts and cron; the
        # browser console never uses it.
        self.api_key = api_key
        self.sessions: dict[str, Session] = {}
        self.transactions: dict[str, dict] = {}
        self.rates: dict[str, list] = {}
        self.http = httpx.AsyncClient(transport=transport, timeout=5.0, follow_redirects=False)
        self.cookie = "__Host-cold_email_session" if config.secure else "cold_email_session_dev"
        self.transaction_cookie = "__Host-cold_email_transaction" if config.secure else "cold_email_transaction_dev"
        self.router = self._routes()

    async def close(self) -> None:
        await self.http.aclose()

    # -- helpers -------------------------------------------------------------

    def _set_cookie(self, response: Response, name: str, value: str, max_age: int) -> None:
        response.set_cookie(name, value, max_age=max_age, path="/", secure=self.config.secure,
                            httponly=True, samesite="lax")

    def _clear_cookie(self, response: Response, name: str) -> None:
        response.delete_cookie(name, path="/", secure=self.config.secure, httponly=True, samesite="lax")

    def _sweep(self) -> None:
        t = self.now()
        for k, s in list(self.sessions.items()):
            if s.expires <= t or s.last_seen + self.config.idle_ttl <= t:
                del self.sessions[k]
        for store in (self.transactions, self.rates):
            for k, v in list(store.items()):
                if (v["expires"] if isinstance(v, dict) else v[0]) <= t:
                    del store[k]

    def _rate(self, request: Request) -> None:
        self._sweep()
        key = f"{request.client.host if request.client else 'unknown'}:{request.url.path}"
        entry = self.rates.get(key)
        if entry is None:
            if len(self.rates) >= self.max_records:
                raise HTTPException(429, "auth_busy", headers=_NO_STORE)
            entry = self.rates[key] = [self.now() + 60, 0]
        entry[1] += 1
        if entry[1] > 120:
            raise HTTPException(429, "too_many_requests", headers=_NO_STORE)

    async def _cms(self, path: str, body: dict) -> dict:
        try:
            r = await self.http.post(
                f"{self.config.api}/auth/sso/{path}",
                json={**body, "client_id": self.config.client_id},
                headers={"Authorization": f"Bearer {self.config.secret}"},
            )
        except httpx.HTTPError:
            raise AuthError(503)
        if r.status_code != 200:
            raise AuthError(403 if r.status_code == 403 else 401 if r.status_code in (400, 401) else 503)
        try:
            return r.json()
        except ValueError:
            raise AuthError(503)

    def _csrf_ok(self, request: Request, session: Session) -> bool:
        return request.headers.get("origin") == self.config.app and _equal(request.headers.get("x-csrf-token"), session.csrf)

    async def _validate(self, request: Request, fresh: Optional[float] = None) -> tuple[str, Session]:
        """The live session for this request. ``fresh`` forces a CMS re-check
        when the last one is older than that many seconds."""
        key = _hash(request.cookies.get(self.cookie, ""))
        session = self.sessions.get(key)

        def alive(s):
            t = self.now()
            return s is not None and self.sessions.get(key) is s and s.expires > t and s.last_seen + self.config.idle_ttl > t

        if not alive(session):
            self.sessions.pop(key, None)
            raise AuthError(401)
        max_age = self.config.role_ttl if fresh is None else min(fresh, self.config.role_ttl)
        if session.checked + max_age <= self.now():
            # One check at a time per session; waiters reuse its result.
            async with session.lock:
                if session.checked + max_age <= self.now():
                    try:
                        # issued_at lets CMS revoke this login after a CMS sign-out.
                        role = (await self._cms("role", {"user_id": session.user["id"], "issued_at": session.issued_at})).get("role")
                        if role not in ROLES:
                            raise AuthError(403)
                    except AuthError as err:
                        if err.status in (401, 403):
                            self.sessions.pop(key, None)
                        raise
                    session.user["role"] = role
                    session.checked = self.now()
        # Logout or revocation may have happened during the network check.
        if not alive(session):
            self.sessions.pop(key, None)
            raise AuthError(401)
        return key, session

    async def require_session(self, request: Request) -> dict:
        """FastAPI dependency guarding every console endpoint."""
        auth = request.headers.get("authorization", "")
        if self.api_key and auth.startswith("Bearer ") and _equal(auth[7:], self.api_key):
            return {"id": "api-key", "email": "", "role": "admin"}
        try:
            _, session = await self._validate(request)
        except AuthError as err:
            raise HTTPException(err.status, _error_body(err.status)["detail"], headers=_NO_STORE)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if not self._csrf_ok(request, session):
                raise HTTPException(403, "invalid_csrf", headers=_NO_STORE)
            session.last_seen = self.now()
        return session.user

    # -- routes --------------------------------------------------------------

    def _routes(self) -> APIRouter:
        router = APIRouter(tags=["auth"])
        cfg = self.config

        @router.get("/start")
        async def start(request: Request, next: str = "/"):
            self._rate(request)
            if len(self.transactions) >= self.max_records:
                return PlainTextResponse("Login busy; retry shortly.", 503, headers=_NO_STORE)
            old = request.cookies.get(self.transaction_cookie)
            if old:
                self.transactions.pop(_hash(old), None)
            browser, state, verifier = (secrets.token_urlsafe(32) for _ in range(3))
            self.transactions[_hash(browser)] = {
                "state": state, "verifier": verifier, "next": local_path(next),
                "expires": self.now() + cfg.transaction_ttl,
            }
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            query = urlencode({"client_id": cfg.client_id, "redirect_uri": f"{cfg.app}/api/auth/callback",
                               "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
            response = RedirectResponse(f"{cfg.cms}/authorize?{query}", 303, headers=_NO_STORE)
            self._set_cookie(response, self.transaction_cookie, browser, cfg.transaction_ttl)
            return response

        @router.get("/callback")
        async def callback(request: Request, code: str = "", state: str = ""):
            self._rate(request)
            key = _hash(request.cookies.get(self.transaction_cookie, ""))
            tx = self.transactions.pop(key, None)

            def fail(message: str, status: int) -> Response:
                r = PlainTextResponse(message, status, headers=_NO_STORE)
                self._clear_cookie(r, self.transaction_cookie)
                return r

            if not tx or tx["expires"] <= self.now() or not _equal(state, tx["state"]) or not _TOKEN.match(code):
                return fail("Invalid or expired login. Return to the console and try again.", 400)
            try:
                data = await self._cms("exchange", {"code": code, "code_verifier": tx["verifier"],
                                                    "redirect_uri": f"{cfg.app}/api/auth/callback"})
                user, issued_at = data.get("user"), data.get("issued_at")
                if not isinstance(user, dict) or not isinstance(user.get("id"), str) \
                        or user.get("role") not in ROLES or not isinstance(issued_at, (int, float)):
                    raise AuthError(403 if isinstance(user, dict) and user.get("role") else 503)
            except AuthError as err:
                if err.status == 403:
                    return fail("Your CMS account does not have access to the cold email console.", 403)
                return fail("Sign-in could not complete. Return to the console and try again.", 503 if err.status == 503 else 400)
            if len(self.sessions) >= self.max_records:
                return fail("Session capacity reached. Retry later.", 503)
            # Rotate any previous session: prevents fixation and orphans.
            self.sessions.pop(_hash(request.cookies.get(self.cookie, "")), None)
            token = secrets.token_urlsafe(32)
            t = self.now()
            self.sessions[_hash(token)] = Session(
                user={"id": user["id"], "email": str(user.get("email") or ""), "role": user["role"]},
                issued_at=float(issued_at), csrf=secrets.token_urlsafe(32),
                expires=t + cfg.session_ttl, last_seen=t, checked=t,
            )
            response = RedirectResponse(tx["next"], 303, headers=_NO_STORE)
            self._clear_cookie(response, self.transaction_cookie)
            self._set_cookie(response, self.cookie, token, cfg.session_ttl)
            return response

        @router.get("/me")
        async def me(request: Request, fresh: str = ""):
            self._rate(request)
            try:
                # ?fresh=1 (tab regained focus): re-check CMS so a CMS sign-out applies at once.
                _, session = await self._validate(request, fresh=5 if fresh == "1" else None)
            except AuthError as err:
                return JSONResponse(_error_body(err.status), err.status, headers=_NO_STORE)
            return JSONResponse({"user": session.user, "csrfToken": session.csrf}, headers=_NO_STORE)

        @router.post("/activity", status_code=204)
        async def activity(request: Request):
            self._rate(request)
            await self.require_session(request)
            return Response(status_code=204, headers=_NO_STORE)

        @router.post("/logout")
        async def logout(request: Request):
            self._rate(request)
            # Works locally even when CMS is down or access was revoked.
            key = _hash(request.cookies.get(self.cookie, ""))
            session = self.sessions.get(key)
            if request.headers.get("origin") != cfg.app or (session and not self._csrf_ok(request, session)):
                return JSONResponse({"detail": "invalid_csrf"}, 403, headers=_NO_STORE)
            self.sessions.pop(key, None)
            # Then end the CMS session too. The ticket lets CMS skip its
            # confirmation prompt; without one (CMS down, no session) CMS asks.
            params = {"client_id": cfg.client_id}
            if session:
                try:
                    ticket = (await self._cms("logout-ticket", {"user_id": session.user["id"]})).get("ticket")
                    if isinstance(ticket, str) and _TOKEN.match(ticket):
                        params["ticket"] = ticket
                except AuthError:
                    pass
            response = JSONResponse({"redirect": f"{cfg.cms}/logout?{urlencode(params)}"}, headers=_NO_STORE)
            self._clear_cookie(response, self.cookie)
            return response

        return router
