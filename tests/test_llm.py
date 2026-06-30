"""Tests for the fidelis LLM layer: providers, factory, spec inference.

Covers:
- ``base.parse_provider_string`` / ``resolve_provider``;
- ``FakeProvider`` (string / callable injection, call tracking);
- ``inference.generate_spec``: anti-hallucination, confidence clamping,
  needs_review, ``_extract_json`` (markdown fences + noise), retry on invalid JSON;
- HTTP providers ``AnthropicProvider`` / ``OpenAIProvider`` via respx.

Network calls NEVER go out to the real world: either FakeProvider or a respx mock.
Each test is independent.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

import fidelis as fp
from conftest import USER_MAPPINGS, User
from fidelis.llm.anthropic import AnthropicProvider
from fidelis.llm.base import (
    LLMProvider,
    parse_provider_string,
    resolve_provider,
)
from fidelis.llm.fake import FakeProvider
from fidelis.llm.inference import (
    MAX_RETRIES,
    _extract_json,
    build_user_prompt,
    generate_spec,
)
from fidelis.llm.local import LocalProvider
from fidelis.llm.openai import OpenAIProvider

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"


# --------------------------------------------------------------------------- #
# parse_provider_string
# --------------------------------------------------------------------------- #


def test_parse_provider_string_basic():
    assert parse_provider_string("anthropic:claude-opus-4-8") == (
        "anthropic",
        "claude-opus-4-8",
    )


def test_parse_provider_string_lowercases_provider_and_strips():
    # Provider is lowercased, the model is only trimmed (case preserved).
    assert parse_provider_string("  OpenAI : GPT-4o ") == ("openai", "GPT-4o")


def test_parse_provider_string_keeps_model_with_extra_colons():
    # Partition on the first ':' — colons within the model stay part of it.
    assert parse_provider_string("local:llama3:8b") == ("local", "llama3:8b")


@pytest.mark.parametrize("bad", ["anthropic", "", ":model", "provider:", ":", "nomodel:"])
def test_parse_provider_string_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_provider_string(bad)


# --------------------------------------------------------------------------- #
# resolve_provider
# --------------------------------------------------------------------------- #


def test_resolve_provider_anthropic():
    prov = resolve_provider("anthropic:claude-opus-4-8", api_key="k")
    assert isinstance(prov, AnthropicProvider)
    assert prov.model == "claude-opus-4-8"


def test_resolve_provider_openai():
    prov = resolve_provider("openai:gpt-4o", api_key="k")
    assert isinstance(prov, OpenAIProvider)
    # LocalProvider is a subclass of OpenAIProvider, so check the exact type.
    assert type(prov) is OpenAIProvider
    assert prov.model == "gpt-4o"


def test_resolve_provider_local():
    prov = resolve_provider("local:llama3")
    assert isinstance(prov, LocalProvider)
    assert prov.model == "llama3"


def test_resolve_provider_fake():
    prov = resolve_provider("fake:fixture")
    assert isinstance(prov, FakeProvider)
    assert prov.model == "fixture"


def test_resolve_provider_passes_through_instance():
    existing = FakeProvider(model="given")
    assert resolve_provider(existing) is existing


def test_resolve_provider_unknown_raises():
    with pytest.raises(ValueError):
        resolve_provider("gemini:pro")


# --------------------------------------------------------------------------- #
# FakeProvider
# --------------------------------------------------------------------------- #


def test_fake_provider_static_response_and_call_count():
    prov = FakeProvider(response='{"mappings": []}')
    assert prov.call_count == 0
    out = prov.complete("SYS", "USR")
    assert out == '{"mappings": []}'
    assert prov.call_count == 1
    assert prov.calls == [("SYS", "USR")]


def test_fake_provider_callable_responder_sees_prompts():
    seen = {}

    def responder(system, user):
        seen["system"] = system
        seen["user"] = user
        return '{"mappings": []}'

    prov = FakeProvider(responder=responder)
    prov.complete("the-system", "the-user")
    assert seen == {"system": "the-system", "user": "the-user"}


def test_fake_provider_mappings_serialized_to_json():
    prov = FakeProvider(mappings=USER_MAPPINGS)
    payload = json.loads(prov.complete("s", "u"))
    assert [m["source"] for m in payload["mappings"]] == ["E-mail", "Name", "Date"]


# --------------------------------------------------------------------------- #
# inference._extract_json
# --------------------------------------------------------------------------- #


def test_extract_json_plain():
    assert _extract_json('{"a": 1, "b": 2}') == {"a": 1, "b": 2}


def test_extract_json_strips_markdown_fences():
    raw = '```json\n{"mappings": [], "parsing": null}\n```'
    assert _extract_json(raw) == {"mappings": [], "parsing": None}


def test_extract_json_strips_bare_fences():
    raw = '```\n{"x": 10}\n```'
    assert _extract_json(raw) == {"x": 10}


def test_extract_json_finds_object_in_noisy_text():
    raw = 'Sure! Here is the answer:\n{"mappings": [{"source": "E-mail"}]}\nDone.'
    assert _extract_json(raw) == {"mappings": [{"source": "E-mail"}]}


def test_extract_json_raises_on_no_json():
    with pytest.raises(json.JSONDecodeError):
        _extract_json("not json at all, no curly braces")


# --------------------------------------------------------------------------- #
# inference.generate_spec — anti-hallucination and post-processing
# --------------------------------------------------------------------------- #


def _gen(provider, field_names, **kw):
    return generate_spec(
        provider,
        field_names=field_names,
        sample=[{f: "v" for f in field_names}],
        model=kw.pop("model", User),
        **kw,
    )


def test_generate_spec_drops_hallucinated_source():
    """A source not present in the field inventory is dropped."""

    mappings = [
        {"source": "E-mail", "target": "email", "confidence": 0.95},
        {"source": "Ghost", "target": "email", "confidence": 0.99},
    ]
    spec = _gen(FakeProvider(mappings=mappings), ["E-mail"])
    sources = [m.source for m in spec.mappings]
    assert sources == ["E-mail"]
    assert "Ghost" not in sources


def test_generate_spec_drops_unknown_target():
    """A target not among the model's fields is dropped."""

    mappings = [
        {"source": "E-mail", "target": "email", "confidence": 0.95},
        {"source": "Name", "target": "not_a_real_field", "confidence": 0.97},
    ]
    spec = _gen(FakeProvider(mappings=mappings), ["E-mail", "Name"])
    targets = [m.target for m in spec.mappings]
    assert targets == ["email"]
    assert "not_a_real_field" not in targets


def test_generate_spec_clamps_confidence_to_unit_interval():
    """Confidence outside [0,1] is clamped; status is computed from the clamped value."""

    mappings = [
        {"source": "E-mail", "target": "email", "confidence": 5.0},
        {"source": "Name", "target": "full_name", "confidence": -2.0},
    ]
    spec = _gen(FakeProvider(mappings=mappings), ["E-mail", "Name"])
    by_target = {m.target: m for m in spec.mappings}
    assert by_target["email"].confidence == 1.0
    assert by_target["email"].status == "ok"
    assert by_target["full_name"].confidence == 0.0
    assert by_target["full_name"].status == "needs_review"


def test_generate_spec_marks_needs_review_below_threshold():
    """Below the threshold — needs_review; at/above the threshold — ok."""

    mappings = [
        {"source": "E-mail", "target": "email", "confidence": 0.95},
        {"source": "Name", "target": "full_name", "confidence": 0.62},
    ]
    spec = _gen(
        FakeProvider(mappings=mappings),
        ["E-mail", "Name"],
        confidence_threshold=0.8,
    )
    by_target = {m.target: m for m in spec.mappings}
    assert by_target["email"].status == "ok"
    assert by_target["full_name"].status == "needs_review"
    assert spec.has_needs_review is True


def test_generate_spec_sets_metadata():
    spec = _gen(FakeProvider(mappings=[]), ["E-mail"])
    assert spec.generated_by == "fake"
    # The signature is computed from the field inventory.
    assert spec.signature == fp.spec.compute_signature(["E-mail"])


# --------------------------------------------------------------------------- #
# inference.generate_spec — retry on invalid JSON
# --------------------------------------------------------------------------- #


def test_generate_spec_retries_after_invalid_json_then_succeeds():
    calls = {"n": 0}

    def responder(system, user):
        calls["n"] += 1
        if calls["n"] == 1:
            return "non-json garbage without braces"
        return json.dumps(
            {"mappings": [{"source": "E-mail", "target": "email", "confidence": 0.9}]}
        )

    prov = FakeProvider(responder=responder)
    spec = _gen(prov, ["E-mail"])
    assert calls["n"] == 2  # exactly one retry
    assert [m.source for m in spec.mappings] == ["E-mail"]


def test_generate_spec_raises_after_exhausting_retries():
    prov = FakeProvider(responder=lambda s, u: "still not json")
    with pytest.raises(ValueError):
        _gen(prov, ["E-mail"])
    # MAX_RETRIES retries => MAX_RETRIES + 1 attempts in total.
    assert prov.call_count == MAX_RETRIES + 1


# --------------------------------------------------------------------------- #
# build_user_prompt — only the sample goes to the LLM
# --------------------------------------------------------------------------- #


def test_build_user_prompt_contains_inventory_and_sample_only():
    sample = [{"E-mail": "sample-marker@x.com", "Name": "Sample Person"}]
    prompt = build_user_prompt(["E-mail", "Name"], sample, User)
    assert "E-mail" in prompt
    assert "sample-marker@x.com" in prompt
    # Only the provided sample makes it into the prompt — no extraneous values.
    assert "row-outside-the-sample" not in prompt


# --------------------------------------------------------------------------- #
# AnthropicProvider via respx
# --------------------------------------------------------------------------- #


@respx.mock
def test_anthropic_request_shape_and_text_extraction():
    route = respx.post(ANTHROPIC_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [
                    {"type": "text", "text": "Hello "},
                    {"type": "tool_use", "name": "x", "input": {}},
                    {"type": "text", "text": "World"},
                ]
            },
        )
    )
    prov = AnthropicProvider(model="claude-opus-4-8", api_key="sk-test")
    out = prov.complete("SYSTEM-PROMPT", "USER-PROMPT")

    # Text is joined only from the text blocks.
    assert out == "Hello World"

    assert route.called
    req = route.calls.last.request
    assert req.headers["x-api-key"] == "sk-test"
    assert req.headers["anthropic-version"]
    body = json.loads(req.content)
    assert body["model"] == "claude-opus-4-8"
    assert body["system"] == "SYSTEM-PROMPT"
    assert body["messages"] == [{"role": "user", "content": "USER-PROMPT"}]


@respx.mock
def test_anthropic_only_sample_goes_into_request_body():
    route = respx.post(ANTHROPIC_URL).mock(
        return_value=httpx.Response(
            200, json={"content": [{"type": "text", "text": '{"mappings": []}'}]}
        )
    )
    prov = AnthropicProvider(api_key="sk-test")
    field_names = ["E-mail", "Name"]
    sample = [{"E-mail": "sample-marker@x.com", "Name": "Sample Person"}]
    generate_spec(
        prov,
        field_names=field_names,
        sample=sample,
        model=User,
    )
    body = json.loads(route.calls.last.request.content)
    user_content = body["messages"][0]["content"]
    # The body holds exactly the prompt built from the sample — no "full dataset".
    assert user_content == build_user_prompt(field_names, sample, User)
    assert "sample-marker@x.com" in user_content


def test_anthropic_missing_api_key_raises_runtime_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    prov = AnthropicProvider(api_key=None)
    with pytest.raises(RuntimeError):
        prov.complete("s", "u")


# --------------------------------------------------------------------------- #
# OpenAIProvider via respx
# --------------------------------------------------------------------------- #


@respx.mock
def test_openai_request_shape_and_text_extraction():
    route = respx.post(OPENAI_URL).mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "RESPONSE-TEXT"}}]},
        )
    )
    prov = OpenAIProvider(model="gpt-4o", api_key="sk-openai")
    out = prov.complete("SYSTEM-PROMPT", "USER-PROMPT")

    assert out == "RESPONSE-TEXT"

    assert route.called
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer sk-openai"
    body = json.loads(req.content)
    assert body["model"] == "gpt-4o"
    assert body["messages"] == [
        {"role": "system", "content": "SYSTEM-PROMPT"},
        {"role": "user", "content": "USER-PROMPT"},
    ]
    # JSON mode is requested explicitly.
    assert body["response_format"] == {"type": "json_object"}


@respx.mock
def test_openai_null_content_becomes_empty_string():
    respx.post(OPENAI_URL).mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": None}}]}
        )
    )
    prov = OpenAIProvider(api_key="sk-openai")
    assert prov.complete("s", "u") == ""


def test_openai_missing_api_key_raises_runtime_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    prov = OpenAIProvider(api_key=None)
    with pytest.raises(RuntimeError):
        prov.complete("s", "u")


@respx.mock
def test_local_provider_uses_default_base_url_and_no_key_needed(monkeypatch):
    monkeypatch.delenv("LOCAL_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LOCAL_LLM_API_KEY", raising=False)
    route = respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}}]}
        )
    )
    prov = LocalProvider(model="llama3")
    # The local provider doesn't require a key — complete does not fail.
    assert prov.complete("s", "u") == "ok"
    assert route.called
