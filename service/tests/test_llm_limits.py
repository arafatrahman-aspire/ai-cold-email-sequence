"""Every LLM call caps its output, so a provider never reserves (or bills)
for the model's maximum: OpenRouter refused calls asking for 65k tokens."""

import pytest

from app.llm.gemini import GeminiClient
from app.llm.omniroute import OmniRouteClient


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_both_drivers_send_the_output_cap(monkeypatch):
    sent = {}

    async def omni_post(self, payload):
        sent["omniroute"] = payload
        return {"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {}}

    async def gemini_post(self, payload):
        sent["gemini"] = payload
        return {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}], "usageMetadata": {}}

    monkeypatch.setattr(OmniRouteClient, "_post", omni_post)
    monkeypatch.setattr(GeminiClient, "_post", gemini_post)

    await OmniRouteClient("k", "http://x", "m", "", 10, max_output_tokens=2048).complete_json("s", "u", temperature=0.5)
    await GeminiClient("k", "m", "http://x", 10, max_output_tokens=2048).complete_json("s", "u", temperature=0.5)

    assert sent["omniroute"]["max_tokens"] == 2048
    assert sent["gemini"]["generationConfig"]["maxOutputTokens"] == 2048


def test_the_default_cap_is_set():
    from app.config import Settings
    assert 2048 <= Settings().llm_max_output_tokens <= 16384
