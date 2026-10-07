"""Google Gemini driver (generativeLanguage REST API)."""

from __future__ import annotations

import logging

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.llm.base import Completion, LLMClient, LLMError

log = logging.getLogger(__name__)


class GeminiClient(LLMClient):
    name = "gemini"

    def __init__(
        self, api_key: str, model: str, base_url: str, timeout: float
    ) -> None:
        if not api_key:
            raise LLMError("GEMINI_API_KEY is not set")
        self._model = model
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={"x-goog-api-key": api_key},
        )

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=15),
        reraise=True,
    )
    async def _post(self, payload: dict) -> dict:
        resp = await self._client.post(
            f"/models/{self._model}:generateContent", json=payload
        )
        # 4xx other than 429 will not succeed on retry.
        if resp.status_code == 429 or resp.status_code >= 500:
            resp.raise_for_status()
        if resp.status_code >= 400:
            raise LLMError(f"gemini {resp.status_code}: {resp.text[:500]}")
        return resp.json()

    async def complete_json(
        self, system: str, user: str, *, temperature: float
    ) -> Completion:
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": temperature,
                "responseMimeType": "application/json",
            },
        }
        try:
            data = await self._post(payload)
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"gemini request failed: {exc}") from exc

        candidates = data.get("candidates") or []
        if not candidates:
            reason = data.get("promptFeedback", {}).get("blockReason", "no candidates")
            raise LLMError(f"gemini returned no completion ({reason})")

        parts = candidates[0].get("content", {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts).strip()
        if not text:
            raise LLMError("gemini returned an empty completion")

        usage = data.get("usageMetadata") or {}
        return Completion(
            text=text, model=self._model, provider=self.name,
            input_tokens=int(usage.get("promptTokenCount") or 0),
            output_tokens=int(usage.get("candidatesTokenCount") or 0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()
