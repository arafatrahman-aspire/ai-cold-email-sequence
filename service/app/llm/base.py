"""LLM driver interface.

Drafting only ever needs one thing from a provider: send a system prompt plus a
user prompt, get JSON back. Keeping the surface this narrow is what makes the
providers swappable by a single environment variable.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


class LLMError(RuntimeError):
    """Raised when a provider fails in a way the caller should handle."""


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    provider: str


class LLMClient(abc.ABC):
    name: str

    @abc.abstractmethod
    async def complete_json(
        self, system: str, user: str, *, temperature: float
    ) -> Completion:
        """Return a completion the caller can parse as JSON."""

    async def aclose(self) -> None:  # pragma: no cover - default no-op
        return None
