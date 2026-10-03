"""Provider selection and cross-provider fallback."""

from __future__ import annotations

import logging
from typing import Optional

from app.config import get_settings
from app.llm.base import Completion, LLMClient, LLMError
from app.llm.gemini import GeminiClient
from app.llm.omniroute import OmniRouteClient

log = logging.getLogger(__name__)


def _build(name: str) -> LLMClient:
    s = get_settings()
    if name == "gemini":
        return GeminiClient(
            api_key=s.gemini_api_key,
            model=s.gemini_model,
            base_url=s.gemini_base_url,
            timeout=s.llm_timeout_seconds,
        )
    if name == "omniroute":
        return OmniRouteClient(
            api_key=s.omniroute_api_key,
            base_url=s.omniroute_base_url,
            model=s.omniroute_model,
            fallback_model=s.omniroute_fallback_model,
            timeout=s.llm_timeout_seconds,
        )
    raise LLMError(f"unknown LLM provider: {name}")


class LLMGateway:
    """Primary provider with an optional cross-provider fallback.

    ``LLM_PROVIDER`` picks the primary; ``LLM_FALLBACK_PROVIDER`` (or "none")
    picks what to try if the primary is down entirely.
    """

    def __init__(self) -> None:
        s = get_settings()
        self._primary = _build(s.llm_provider)
        self._fallback: Optional[LLMClient] = None
        if s.llm_fallback_provider != "none":
            if s.llm_fallback_provider == s.llm_provider:
                log.warning("fallback provider equals primary; ignoring fallback")
            else:
                try:
                    self._fallback = _build(s.llm_fallback_provider)
                except LLMError as exc:
                    log.warning("fallback provider unavailable: %s", exc)

    async def complete_json(
        self, system: str, user: str, *, temperature: float
    ) -> Completion:
        try:
            return await self._primary.complete_json(
                system, user, temperature=temperature
            )
        except Exception as exc:
            if self._fallback is None:
                raise
            log.warning(
                "primary provider %s failed (%s); falling back to %s",
                self._primary.name, exc, self._fallback.name,
            )
            return await self._fallback.complete_json(
                system, user, temperature=temperature
            )

    async def aclose(self) -> None:
        await self._primary.aclose()
        if self._fallback is not None:
            await self._fallback.aclose()


_gateway: Optional[LLMGateway] = None


def get_gateway() -> LLMGateway:
    global _gateway
    if _gateway is None:
        _gateway = LLMGateway()
    return _gateway


async def close_gateway() -> None:
    global _gateway
    if _gateway is not None:
        await _gateway.aclose()
        _gateway = None
