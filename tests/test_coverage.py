"""Tests for run-level coverage (#7): ParseResult.coverage."""

from __future__ import annotations

import fidelis as fp
from fidelis import Coverage

from conftest import User


def _user_spec(spec_dir):
    fp.Spec(
        signature=fp.compute_signature(["E-mail", "Name", "Date"]),
        mappings=[
            fp.Mapping(target="email", source="E-mail", transform="strip_lower"),
            fp.Mapping(target="full_name", source="Name", transform="strip"),
            fp.Mapping(target="signup_date", source="Date", transform="parse_date:%d.%m.%Y"),
        ],
    ).save(spec_dir)


def test_coverage_all_good(spec_dir):
    _user_spec(spec_dir)
    result = fp.Parser(User, spec_store=spec_dir, llm=None).parse([
        {"E-mail": "a@b.com", "Name": "Alice", "Date": "01.02.2026"},
        {"E-mail": "b@c.com", "Name": "Bob", "Date": "15.03.2026"},
    ])
    cov = result.coverage
    assert cov.rows_in == 2 and cov.rows_with_output == 2
    assert cov.rows_with_error == 0 and cov.records_out == 2
    assert cov.score == 1.0


def test_coverage_with_some_errors(spec_dir):
    _user_spec(spec_dir)
    result = fp.Parser(User, spec_store=spec_dir, llm=None).parse([
        {"E-mail": "a@b.com", "Name": "Alice", "Date": "01.02.2026"},
        {"E-mail": "b@c.com", "Name": "Bob", "Date": "nope"},      # transform fails
        {"E-mail": "c@d.com", "Name": "Carol", "Date": "20.04.2026"},
    ])
    cov = result.coverage
    assert cov.rows_in == 3
    assert cov.rows_with_output == 2
    assert cov.rows_with_error == 1
    assert round(cov.score, 4) == round(2 / 3, 4)


def test_coverage_empty_input_is_one(spec_dir):
    _user_spec(spec_dir)
    # Header only, zero data rows (same field signature → spec matches).
    result = fp.Parser(User, spec_store=spec_dir, llm=None).parse("E-mail;Name;Date\n")
    assert result.coverage.rows_in == 0
    assert result.coverage.score == 1.0


def test_coverage_default_and_str():
    c = Coverage(rows_in=10, rows_with_output=8, rows_with_error=2, records_out=8)
    assert c.score == 0.8
    assert "8/10" in str(c) and "coverage=0.80" in str(c)


def test_coverage_in_summary(spec_dir):
    _user_spec(spec_dir)
    result = fp.Parser(User, spec_store=spec_dir, llm=None).parse([
        {"E-mail": "a@b.com", "Name": "Alice", "Date": "01.02.2026"},
    ])
    assert "coverage=1.00" in result.summary()
