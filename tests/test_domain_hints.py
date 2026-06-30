"""Tests for domain hints passed into LLM spec generation (#6).

Hints only affect the generation prompt — the deterministic parse path is
untouched. We assert the hints reach the prompt (free-text and structured), and
that a Parser(domain_hints=...) threads them through generate_spec.
"""

from __future__ import annotations

from datetime import date

import fidelis as fp
from fidelis.llm.inference import build_user_prompt

from conftest import USER_MAPPINGS, User


def test_build_prompt_includes_free_text_hints():
    prompt = build_user_prompt(["A", "B"], [{"A": "1"}], User, "this is a product catalog")
    assert "Domain context" in prompt
    assert "this is a product catalog" in prompt


def test_build_prompt_includes_structured_hints():
    hints = {"currencies": ["USD", "EUR"], "categories": ["A", "B", "C"]}
    prompt = build_user_prompt(["A"], [{"A": "1"}], User, hints)
    assert "Domain context" in prompt
    assert "USD" in prompt and "categories" in prompt


def test_build_prompt_omits_section_when_no_hints():
    prompt = build_user_prompt(["A"], [{"A": "1"}], User, None)
    assert "Domain context" not in prompt


def test_parser_passes_hints_to_generation(spec_dir):
    # A recording FakeProvider captures the user prompt it is asked to complete.
    class RecordingFake(fp.FakeProvider):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.last_user = None

        def complete(self, system, user):
            self.last_user = user
            return super().complete(system, user)

    provider = RecordingFake(mappings=USER_MAPPINGS)
    parser = fp.Parser(
        User,
        spec_store=spec_dir,
        llm=provider,
        domain_hints={"dates": "always in the 2026 fiscal year"},
    )
    result = parser.parse(
        [{"E-mail": "a@b.com", "Name": "Alice", "Date": "01.02.2026"}]
    )
    assert result.spec_generated is True
    assert "Domain context" in provider.last_user
    assert "2026 fiscal year" in provider.last_user


def test_hints_do_not_affect_deterministic_parse(spec_dir):
    # With a spec already present, domain_hints are irrelevant (no LLM call).
    fp.Spec(
        signature=fp.compute_signature(["E-mail", "Name", "Date"]),
        mappings=[
            fp.Mapping(target="email", source="E-mail", transform="strip_lower"),
            fp.Mapping(target="full_name", source="Name", transform="strip"),
            fp.Mapping(target="signup_date", source="Date", transform="parse_date:%d.%m.%Y"),
        ],
    ).save(spec_dir)
    parser = fp.Parser(User, spec_store=spec_dir, llm=None, domain_hints="ignored here")
    result = parser.parse([{"E-mail": "a@b.com", "Name": "Alice", "Date": "01.02.2026"}])
    assert result.spec_generated is False
    assert result.valid_rows[0] == User(
        email="a@b.com", full_name="Alice", signup_date=date(2026, 2, 1)
    )
