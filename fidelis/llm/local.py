"""LocalProvider — any OpenAI-compatible endpoint (Ollama, vLLM, LM Studio…)."""

from __future__ import annotations

import os
from typing import Optional

from .openai import OpenAIProvider

_DEFAULT_BASE_URL = "http://localhost:11434/v1/chat/completions"


class LocalProvider(OpenAIProvider):
    """OpenAI-compatible local endpoint.

    Differs from :class:`OpenAIProvider` in its default ``base_url`` and in that
    an api key is optional (local servers usually don't require one).
    """

    def __init__(
        self,
        model: str = "llama3",
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        max_tokens: int = 2048,
        timeout: float = 120.0,
    ):
        super().__init__(
            model=model,
            api_key=api_key or os.environ.get("LOCAL_LLM_API_KEY") or "not-needed",
            base_url=base_url or os.environ.get("LOCAL_LLM_BASE_URL", _DEFAULT_BASE_URL),
            max_tokens=max_tokens,
            timeout=timeout,
        )

    def complete(self, system: str, user: str) -> str:
        # Local servers may not support response_format — but most
        # OpenAI-compatible ones accept it; response parsing is tolerant anyway.
        return super().complete(system, user)
