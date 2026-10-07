"""OmniRoute driver.

OmniRoute is addressed as an OpenAI-compatible gateway: the same
``/chat/completions`` contract, with routing between OpenAI (primary) and
Anthropic (fallback) expressed as a model name. If your gateway exposes a
different path or auth header, those are the only two lines to change.
"""

from __future__ import annotations

import logging

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.llm.base import Completion, LLMClient, LLMError

log = logging.getLogger(__name__)


class OmniRouteClient(LLMClient):
    name = "omniroute"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        fallback_model: str,
        timeout: float,
    ) -> None:
        if not base_url:
            raise LLMError("OMNIROUTE_BASE_URL is not set")
        self._model = model
        self._fallback_model = fallback_model
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=15),
        reraise=True,
    )
    async def _post(self, payload: dict) -> dict:
        resp = await self._client.post("/chat/completions", json=payload)
        if resp.status_code == 429 or resp.status_code >= 500:
            resp.raise_for_status()
        if resp.status_code >= 400:
            raise LLMError(f"omniroute {resp.status_code}: {resp.text[:500]}")
        return resp.json()

    async def _call(self, model: str, system: str, user: str, temperature: float) -> Completion:
        payload = {
            "model": model,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        data = await self._post(payload)
        choices = data.get("choices") or []
        if not choices:
            raise LLMError("omniroute returned no choices")
        text = (choices[0].get("message") or {}).get("content", "").strip()
        if not text:
            raise LLMError("omniroute returned an empty completion")
        usage = data.get("usage") or {}
        return Completion(
            text=text, model=model, provider=self.name,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
        )

    async def complete_json(
        self, system: str, user: str, *, temperature: float
    ) -> Completion:
        try:
            return await self._call(self._model, system, user, temperature)
        except (LLMError, httpx.HTTPError) as exc:
            if not self._fallback_model or self._fallback_model == self._model:
                raise LLMError(f"omniroute primary failed: {exc}") from exc
            log.warning(
                "omniroute primary model %s failed (%s); trying fallback %s",
                self._model, exc, self._fallback_model,
            )
            return await self._call(
                self._fallback_model, system, user, temperature
            )

    async def aclose(self) -> None:
        await self._client.aclose()
