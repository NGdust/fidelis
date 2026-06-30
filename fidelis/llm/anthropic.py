"""AnthropicProvider — one-shot schema inference via the Anthropic Messages API."""

from __future__ import annotations

import os
from typing import Optional

from .base import LLMProvider

_API_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"


class AnthropicProvider(LLMProvider):
    """Provider on top of the Anthropic Messages API (HTTP via httpx)."""

    def __init__(
        self,
        model: str = "claude-opus-4-8",
        *,
        api_key: Optional[str] = None,
        base_url: str = _API_URL,
        max_tokens: int = 2048,
        timeout: float = 60.0,
    ):
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.base_url = base_url
        self.max_tokens = max_tokens
        self.timeout = timeout

    def complete(self, system: str, user: str) -> str:
        if not self.api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set (env or api_key=...)."
            )
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "AnthropicProvider requires httpx: pip install 'fidelis[anthropic]'"
            ) from exc

        resp = httpx.post(
            self.base_url,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": _API_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": self.max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        parts = data.get("content", [])
        return "".join(
            block.get("text", "") for block in parts if block.get("type") == "text"
        )
