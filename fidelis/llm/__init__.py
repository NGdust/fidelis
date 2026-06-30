"""LLM layer: one-shot schema inference, provider-agnostic."""

from .anthropic import AnthropicProvider
from .base import LLMProvider, parse_provider_string, resolve_provider
from .fake import FakeProvider
from .inference import DEFAULT_CONFIDENCE_THRESHOLD, generate_spec
from .local import LocalProvider
from .openai import OpenAIProvider

__all__ = [
    "LLMProvider",
    "resolve_provider",
    "parse_provider_string",
    "AnthropicProvider",
    "OpenAIProvider",
    "LocalProvider",
    "FakeProvider",
    "generate_spec",
    "DEFAULT_CONFIDENCE_THRESHOLD",
]
