"""OpenAIProvider — one-shot schema inference via the OpenAI Chat Completions API."""

from __future__ import annotations

import os
from typing import Optional

from .base import LLMProvider

_API_URL = "https://api.openai.com/v1/chat/completions"


class OpenAIProvider(LLMProvider):
    """Provider on top of the OpenAI Chat Completions API (HTTP via httpx)."""

    def __init__(
        self,
        model: str = "gpt-4o",
        *,
        api_key: Optional[str] = None,
        base_url: str = _API_URL,
        max_tokens: int = 2048,
        timeout: float = 60.0,
    ):
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.base_url = base_url
        self.max_tokens = max_tokens
        self.timeout = timeout

    def complete(self, system: str, user: str) -> str:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not set (env or api_key=...).")
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "OpenAIProvider requires httpx: pip install 'fidelis[openai]'"
            ) from exc

        resp = httpx.post(
            self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": self.max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": {"type": "json_object"},
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"] or ""
