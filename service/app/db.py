"""Database access through the Supabase REST API.

The service never opens a Postgres connection. Every operation is a function
in the ``cold_email`` schema (see supabase/migrations/0001_init.sql), invoked
as ``POST {SUPABASE_URL}/rest/v1/rpc/<name>`` with the service-role key. Each
call runs in its own transaction on the server, which is what keeps the
claim / reserve / record paths atomic without a client-side transaction.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any, Optional

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

SCHEMA = "cold_email"

_client: Optional[httpx.AsyncClient] = None


class DatabaseError(RuntimeError):
    pass


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _headers(key: str) -> dict[str, str]:
    headers = {
        "apikey": key,
        "Content-Type": "application/json",
        # Target the cold_email schema instead of public.
        "Content-Profile": SCHEMA,
        "Accept-Profile": SCHEMA,
    }
    # New-style keys (sb_secret_...) are not JWTs and go on the apikey header
    # only. A legacy service_role key is a JWT and must also be the bearer.
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    return headers


async def connect() -> httpx.AsyncClient:
    global _client
    if _client is None:
        s = get_settings()
        _client = httpx.AsyncClient(
            base_url=f"{s.supabase_url.rstrip('/')}/rest/v1",
            headers=_headers(s.supabase_service_role_key),
            timeout=s.db_timeout_seconds,
        )
        log.info("supabase REST client ready")
    return _client


async def disconnect() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
        log.info("supabase REST client closed")


def client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("database client not initialised; call connect() first")
    return _client


async def rpc(function: str, **params: Any) -> Any:
    """Call ``cold_email.<function>`` with named arguments; return its JSON result."""
    response = await client().post(
        f"/rpc/{function}", content=json.dumps(params, default=_json_default)
    )
    if response.status_code >= 400:
        try:
            body = response.json()
            detail = body.get("message") or response.text
            code = body.get("code", "")
        except ValueError:
            detail, code = response.text, ""
        if code == "PGRST106" or "schema must be one of" in str(detail):
            detail = (
                f"{detail} — add '{SCHEMA}' under Supabase > Project Settings >"
                " Data API > Exposed schemas"
            )
        if code == "PGRST202":
            detail = (
                f"database function {SCHEMA}.{function} not found — run the newest"
                " file in supabase/migrations in the Supabase SQL Editor"
            )
        detail = str(detail)[:500]
        raise DatabaseError(f"{function}: HTTP {response.status_code} {code} {detail}".strip())
    if not response.content:
        return None
    return response.json()


async def rows(function: str, **params: Any) -> list[dict[str, Any]]:
    """Call a set-returning function; always returns a list."""
    return await rpc(function, **params) or []
