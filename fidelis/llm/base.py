"""Provider-agnostic LLM interface and a factory keyed by a string like ``"anthropic:model"``."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class LLMProvider(ABC):
    """Abstract provider. An implementation performs exactly one one-shot schema inference.

    The contract is intentionally narrow: a single method that, given a system
    and a user prompt, returns the model's raw response text. All the logic for
    building the prompt, parsing, and anti-hallucination lives above the provider
    (see :mod:`fidelis.llm.inference`).
    """

    #: Model identifier (for example, ``claude-opus-4-8``).
    model: str

    @abstractmethod
    def complete(self, system: str, user: str) -> str:
        """Return the model's raw text response for a pair of prompts."""
        raise NotImplementedError

    @property
    def label(self) -> str:
        return f"{type(self).__name__}({self.model})"


def parse_provider_string(spec: str) -> tuple[str, str]:
    """Parse ``"anthropic:claude-opus-4-8"`` into ``("anthropic", "claude-opus-4-8")``."""

    provider, sep, model = spec.partition(":")
    if not sep or not provider or not model:
        raise ValueError(
            f"LLM string must look like 'provider:model', got {spec!r}"
        )
    return provider.strip().lower(), model.strip()


def resolve_provider(spec: str | LLMProvider, **kwargs) -> LLMProvider:
    """Build a provider from a ``"provider:model"`` string or return a ready-made object.

    Supported providers: ``anthropic``, ``openai``, ``local``, ``fake``.
    ``kwargs`` (api_key, base_url, ...) are forwarded to the constructor.
    """

    if isinstance(spec, LLMProvider):
        return spec

    provider, model = parse_provider_string(spec)

    if provider == "anthropic":
        from .anthropic import AnthropicProvider

        return AnthropicProvider(model=model, **kwargs)
    if provider == "openai":
        from .openai import OpenAIProvider

        return OpenAIProvider(model=model, **kwargs)
    if provider == "local":
        from .local import LocalProvider

        return LocalProvider(model=model, **kwargs)
    if provider == "fake":
        from .fake import FakeProvider

        return FakeProvider(model=model, **kwargs)

    raise ValueError(f"Unknown LLM provider: {provider!r}")
