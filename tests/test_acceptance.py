"""MVP acceptance criteria (PRD section 16) — one test per criterion."""

from __future__ import annotations

from datetime import date

import pytest

import fidelis as fp
from conftest import USER_MAPPINGS, User


def _provider():
    return fp.FakeProvider(mappings=USER_MAPPINGS)


def test_one_parse_handles_csv_and_records(spec_dir, user_records, user_csv_text):
    """[1] A single parse() correctly handles both a CSV file and a list[dict]."""

    parser = fp.Parser(User, spec_store=spec_dir, llm=_provider())

    r_records = parser.parse(user_records)
    r_csv = parser.parse(user_csv_text, kind="csv")

    assert isinstance(r_records, fp.ParseResult)
    assert isinstance(r_csv, fp.ParseResult)
    # Same spec (one field signature) for both inputs.
    assert r_records.spec_used.signature == r_csv.spec_used.signature
    # The first record matches in substance.
    assert r_records.valid_rows[0] == User(
        email="a@b.com", full_name="Alice", signup_date=date(2026, 2, 1)
    )
    assert r_csv.valid_rows[0] == r_records.valid_rows[0]


def test_spec_generated_once_then_no_llm(spec_dir, user_records):
    """[2] No spec → a YAML draft is generated; a rerun does NOT call the LLM."""

    provider = _provider()
    parser = fp.Parser(User, spec_store=spec_dir, llm=provider)

    r1 = parser.parse(user_records)
    assert r1.spec_generated is True
    assert provider.call_count == 1
    assert (spec_dir / f"spec_{r1.spec_used.signature}.yaml").exists()

    r2 = parser.parse(user_records)
    assert r2.spec_generated is False
    assert provider.call_count == 1  # the LLM was not called again


def test_low_confidence_marks_needs_review(spec_dir, user_records):
    """[3] Low-confidence mappings are flagged needs_review and surfaced in the result."""

    parser = fp.Parser(User, spec_store=spec_dir, llm=_provider(), confidence_threshold=0.8)
    result = parser.parse(user_records)

    assert result.needs_review is True
    flagged = [m for m in result.spec_used.mappings if m.status == "needs_review"]
    assert any(m.target == "signup_date" for m in flagged)  # confidence 0.62 < 0.8


def test_bad_rows_go_to_errors_not_lost(spec_dir):
    """[4] Bad rows go to errors instead of being silently dropped."""

    records = [
        {"E-mail": "ok@x.com", "Name": "Good", "Date": "01.02.2026"},
        {"E-mail": "bad", "Name": "Bad", "Date": "not-a-date"},
    ]
    parser = fp.Parser(User, spec_store=spec_dir, llm=_provider())
    result = parser.parse(records)

    assert len(result.valid_rows) == 1
    assert len(result.errors) == 1
    err = result.errors[0]
    assert err.row_index == 1
    assert err.field == "signup_date"
    # Every row is accounted for — nothing silently lost.
    assert len(result.valid_rows) + len({e.row_index for e in result.errors}) == 2


def test_new_field_errors_with_drift_report(spec_dir, user_records):
    """[5] A new field with on_unknown_column=error → clear error + drift_report."""

    parser = fp.Parser(User, spec_store=spec_dir, llm=_provider(), on_unknown_column="error")
    parser.parse(user_records)  # creates the spec

    drifted = [{"E-mail": "a@b.com", "Name": "A", "Date": "01.02.2026", "Extra": "x"}]
    with pytest.raises(fp.DriftError) as exc:
        parser.parse(drifted)
    assert exc.value.report.has_drift is True
    assert "Extra" in exc.value.report.new_fields


def test_llm_receives_only_headers_and_sample(spec_dir):
    """[6] The LLM receives only headers + a sample, never the entire dataset."""

    big = [
        {"E-mail": f"u{i}@x.com", "Name": f"N{i}", "Date": "01.02.2026"}
        for i in range(1000)
    ]
    provider = fp.FakeProvider(mappings=USER_MAPPINGS)
    parser = fp.Parser(User, spec_store=spec_dir, llm=provider, sample_size=20)
    parser.parse(big)

    assert provider.call_count == 1
    _system, user_prompt = provider.calls[0]
    # The prompt contains exactly sample_size records, not 1000.
    assert "first 20 records" in user_prompt
    assert "u20@x.com" not in user_prompt  # the 21st record (index 20) is no longer in the sample
    assert "u0@x.com" in user_prompt
