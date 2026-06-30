"""Tests for fidelis/enrichment.py and its wiring through the Parser.

Two layers:
- the registry/apply helpers in isolation (register, decorator form, resolve,
  duplicate protection, return-contract);
- end-to-end through Parser(enrich=[...]): an enricher derives a field absent
  from the source, combines two mapped fields, and a failing enricher turns the
  row into a RowError instead of silently dropping it.

An autouse fixture snapshots and restores the global enrichment registry so
custom registrations cannot leak between tests.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import BaseModel

import fidelis as fp
from fidelis import enrichment as E
from fidelis.enrichment import (
    EnrichmentError,
    apply_enrichment,
    available_enrichments,
    register_enrichment,
    resolve_enrichment,
)

from conftest import User


@pytest.fixture(autouse=True)
def _restore_registry():
    """Keep the global enrichment registry isolated per test."""
    snapshot = dict(E._REGISTRY)
    try:
        yield
    finally:
        E._REGISTRY.clear()
        E._REGISTRY.update(snapshot)


# --------------------------------------------------------------------------- #
# register_enrichment / available_enrichments
# --------------------------------------------------------------------------- #


def test_register_direct_form_usable():
    register_enrichment("tag", lambda rec, src: {**rec, "tag": "x"})
    assert "tag" in available_enrichments()
    name, fn = resolve_enrichment("tag")
    assert name == "tag"
    assert fn({"a": 1}, {}) == {"a": 1, "tag": "x"}


def test_register_decorator_form_returns_function():
    @register_enrichment("deco")
    def enrich(record, source):
        record["seen"] = True
        return record

    # The decorator returns the original function unchanged.
    assert enrich({"a": 1}, {}) == {"a": 1, "seen": True}
    assert "deco" in available_enrichments()


def test_available_enrichments_sorted():
    register_enrichment("b", lambda r, s: r)
    register_enrichment("a", lambda r, s: r)
    names = available_enrichments()
    assert names == sorted(names)
    assert {"a", "b"} <= set(names)


def test_register_duplicate_without_overwrite_raises():
    register_enrichment("dup", lambda r, s: r)
    with pytest.raises(ValueError):
        register_enrichment("dup", lambda r, s: {"clobbered": True})


def test_register_duplicate_with_overwrite_replaces():
    register_enrichment("dup2", lambda r, s: {"v": 1})
    register_enrichment("dup2", lambda r, s: {"v": 2}, overwrite=True)
    _name, fn = resolve_enrichment("dup2")
    assert fn({}, {}) == {"v": 2}


# --------------------------------------------------------------------------- #
# resolve_enrichment
# --------------------------------------------------------------------------- #


def test_resolve_unknown_name_raises():
    with pytest.raises(EnrichmentError):
        resolve_enrichment("does_not_exist")


def test_resolve_callable_passthrough():
    def my_enricher(record, source):
        return record

    name, fn = resolve_enrichment(my_enricher)
    assert name == "my_enricher"
    assert fn is my_enricher


def test_resolve_non_callable_non_string_raises():
    with pytest.raises(EnrichmentError):
        resolve_enrichment(42)


# --------------------------------------------------------------------------- #
# apply_enrichment return contract
# --------------------------------------------------------------------------- #


def test_apply_returns_new_dict():
    out = apply_enrichment(lambda rec, src: {**rec, "k": 1}, {"a": 0}, {})
    assert out == {"a": 0, "k": 1}


def test_apply_none_means_mutated_in_place():
    def mutate(record, source):
        record["k"] = 1  # no return

    rec = {"a": 0}
    out = apply_enrichment(mutate, rec, {})
    assert out is rec
    assert out == {"a": 0, "k": 1}


def test_apply_non_dict_return_raises():
    with pytest.raises(EnrichmentError):
        apply_enrichment(lambda rec, src: "not-a-dict", {}, {})


def test_apply_enrichment_receives_source_record():
    captured = {}

    def grab(record, source):
        captured.update(source)
        return record

    apply_enrichment(grab, {"email": "a@b.com"}, {"E-mail": "raw"})
    assert captured == {"E-mail": "raw"}


# --------------------------------------------------------------------------- #
# End-to-end through the Parser
# --------------------------------------------------------------------------- #


def _user_spec(spec_dir):
    """A ready User spec matching the user_records signature, saved to disk."""

    spec = fp.Spec(
        signature=fp.compute_signature(["E-mail", "Name", "Date"]),
        mappings=[
            fp.Mapping(target="email", source="E-mail", transform="strip_lower"),
            fp.Mapping(target="full_name", source="Name", transform="strip"),
            fp.Mapping(
                target="signup_date", source="Date", transform="parse_date:%d.%m.%Y"
            ),
        ],
    )
    spec.save(spec_dir)
    return spec


class Account(BaseModel):
    """Target model with a `domain` field that has NO source column."""

    email: str
    domain: str


def test_enrichment_derives_field_without_source_column(spec_dir):
    """An enricher fills a target field that no source column maps to."""

    fp.Spec(
        signature=fp.compute_signature(["E-mail"]),
        mappings=[fp.Mapping(target="email", source="E-mail", transform="strip_lower")],
    ).save(spec_dir)

    @register_enrichment("fill_domain")
    def _fill_domain(record, source):
        record["domain"] = record["email"].split("@", 1)[1]
        return record

    parser = fp.Parser(Account, spec_store=spec_dir, llm=None, enrich=["fill_domain"])
    result = parser.parse([{"E-mail": " A@B.COM "}, {"E-mail": "bob@x.com"}])

    assert result.errors == []
    assert result.valid_rows[0] == Account(email="a@b.com", domain="b.com")
    assert result.valid_rows[1] == Account(email="bob@x.com", domain="x.com")


def test_enrichment_receives_full_record(spec_dir):
    """An enricher can read several mapped fields at once (whole-record view)."""

    _user_spec(spec_dir)

    seen = {}

    def capture(record, source):
        seen.update(record)
        record["full_name"] = record["full_name"].upper()
        return record

    parser = fp.Parser(User, spec_store=spec_dir, llm=None, enrich=[capture])
    result = parser.parse([{"E-mail": " A@B.COM ", "Name": " Alice ", "Date": "01.02.2026"}])

    # The enricher saw every mapped field, not just one value.
    assert set(seen) == {"email", "full_name", "signup_date"}
    assert result.valid_rows[0] == User(
        email="a@b.com", full_name="ALICE", signup_date=date(2026, 2, 1)
    )


def test_enrichers_run_in_order(spec_dir):
    """Multiple enrichers chain, each seeing the previous one's output."""

    _user_spec(spec_dir)
    register_enrichment("step1", lambda r, s: {**r, "full_name": r["full_name"] + "-1"})
    register_enrichment("step2", lambda r, s: {**r, "full_name": r["full_name"] + "-2"})

    parser = fp.Parser(User, spec_store=spec_dir, llm=None, enrich=["step1", "step2"])
    result = parser.parse([{"E-mail": "a@b.com", "Name": "Alice", "Date": "01.02.2026"}])

    assert result.valid_rows[0].full_name == "Alice-1-2"


def test_failing_enrichment_becomes_row_error(spec_dir):
    """An exception in an enricher produces a RowError, not a silent drop."""

    _user_spec(spec_dir)

    @register_enrichment("boom")
    def _boom(record, source):
        raise ValueError("kaboom")

    parser = fp.Parser(User, spec_store=spec_dir, llm=None, enrich=["boom"])
    result = parser.parse([{"E-mail": "a@b.com", "Name": "Alice", "Date": "01.02.2026"}])

    assert result.valid_rows == []
    assert len(result.errors) == 1
    err = result.errors[0]
    assert err.row_index == 0
    assert "boom" in err.reason and "kaboom" in err.reason


def test_unknown_enrichment_name_fails_at_construction(spec_dir):
    """An unknown enricher name is rejected when the Parser is built, not per row."""

    with pytest.raises(EnrichmentError):
        fp.Parser(User, spec_store=spec_dir, llm=None, enrich=["nope"])


def test_no_enrichers_is_a_noop(spec_dir):
    """Omitting enrich leaves the pipeline unchanged."""

    _user_spec(spec_dir)
    parser = fp.Parser(User, spec_store=spec_dir, llm=None)
    result = parser.parse([{"E-mail": " A@B.COM ", "Name": " Alice ", "Date": "01.02.2026"}])
    assert result.valid_rows[0] == User(
        email="a@b.com", full_name="Alice", signup_date=date(2026, 2, 1)
    )
