"""Process configuration, read once from the environment.

Anything that operators may want to change without a redeploy lives in the
``cold_email.settings`` table instead (see :mod:`app.settings_store`). This module is
only for wiring: connection strings, provider selection, and secrets.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Settings below only covers the fixed fields. Per-inbox secrets are named at
# runtime (INBOX_A_SMTP_PASSWORD, ...) and read from os.environ, so .env must
# be loaded into the environment too, or they are missing when the service is
# started without Docker. Variables already set in the environment win.
load_dotenv(".env", override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Database (Supabase REST API) --------------------------------------
    # All access goes through cold_email.* functions over the REST API, so the
    # secret (service-role) key is what the service uses. The publishable key
    # is accepted for completeness but has no rights in cold_email.
    supabase_url: str
    supabase_service_role_key: str
    supabase_publishable_key: str = ""
    db_timeout_seconds: float = 30.0

    # --- LLM gateway --------------------------------------------------------
    # Which driver drafts the emails. Both are always importable; only the
    # selected one needs credentials.
    llm_provider: Literal["gemini", "omniroute"] = "gemini"
    llm_fallback_provider: Literal["gemini", "omniroute", "none"] = "none"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    # OmniRoute is addressed as an OpenAI-compatible gateway.
    omniroute_api_key: str = ""
    omniroute_base_url: str = ""
    omniroute_model: str = "gpt-4o"
    omniroute_fallback_model: str = "claude-sonnet-5"

    llm_timeout_seconds: float = 90.0
    llm_temperature: float = 0.7

    # --- Mail transport -----------------------------------------------------
    # "smtp"  -> plain SMTP submission (VPS relay, Outlook, Zoho, anything)
    # "graph" -> Microsoft Graph /sendMail with an Entra app
    mail_sender: Literal["smtp", "graph"] = "smtp"
    # "imap"  -> IMAP polling
    # "graph" -> Microsoft Graph /messages polling
    mail_reader: Literal["imap", "graph"] = "imap"

    # SMTP defaults; an inbox row may override host/port/tls in its config JSON.
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    smtp_timeout_seconds: float = 30.0

    # IMAP defaults, same override rule.
    imap_host: str = "localhost"
    imap_port: int = 993
    imap_ssl: bool = True
    imap_folder: str = "INBOX"
    imap_timeout_seconds: float = 60.0

    # Microsoft Graph (only needed when a provider is "graph").
    graph_tenant_id: str = ""
    graph_client_id: str = ""
    graph_client_secret: str = ""

    # --- Sending identity ---------------------------------------------------
    unsubscribe_mailto: str = ""
    reply_to: str = ""

    # --- Calendar (meeting booking for reply triage) -----------------------
    # "calcom" books through Cal.com; "fake" is an in-memory calendar for
    # testing; "none" offers no slots (drafts ask the lead for times).
    # Google / Microsoft slot in as further providers (app/calendar/).
    calendar_provider: Literal["none", "fake", "calcom", "google", "microsoft"] = "none"
    calendar_timeout_seconds: float = 20.0
    calcom_api_key: str = ""
    calcom_event_type_id: int = 0
    # Public booking page to put in emails, e.g. https://cal.com/you/30min
    calcom_booking_url: str = ""
    calcom_base_url: str = "https://api.cal.com"
    calcom_slots_api_version: str = "2024-09-04"
    calcom_bookings_api_version: str = "2026-02-25"
    # Fake calendar: meeting length and the host's working timezone.
    fake_calendar_timezone: str = "UTC"
    fake_calendar_booking_url: str = "https://cal.example.com/demo/30min"

    # --- Scheduler ----------------------------------------------------------
    run_workers: bool = True
    intake_interval_seconds: int = 300
    send_interval_seconds: int = 180
    poll_interval_seconds: int = 420
    triage_interval_seconds: int = 60

    # --- Misc ---------------------------------------------------------------
    dry_run: bool = False
    log_level: str = "INFO"
    # Optional bearer token for scripts/cron. The console itself signs in
    # through CMS (APP_PUBLIC_URL, CMS_PUBLIC_URL, SSO_CLIENT_SECRET; app/auth.py).
    api_key: str = ""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
