"""Tests for batch enrichment (fidelis/enrichment.py + Parser wiring).

Batch enrichers see every clean mapped record at once and must return a list of
the same length, in order. Covered here:
- registry/resolve/apply helpers and the one-to-one contract;
- end-to-end through Parser(batch_enrich=[...]): a single call fills a field
  across the whole feed, error rows are excluded and preserved, and a contract
  violation raises BatchEnrichmentError.

An autouse fixture isolates both enrichment registries per test.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

import fidelis as fp
from fidelis import enrichment as E
from fidelis.enrichment import (
    BatchEnrichmentError,
    apply_batch_enrichment,
    available_batch_enrichments,
    register_batch_enrichment,
    resolve_batch_enrichment,
)

from conftest import User


@pytest.fixture(autouse=True)
def _restore_registries():
    snap, bsnap = dict(E._REGISTRY), dict(E._BATCH_REGISTRY)
    try:
        yield
    finally:
        E._REGISTRY.clear()
        E._REGISTRY.update(snap)
        E._BATCH_REGISTRY.clear()
        E._BATCH_REGISTRY.update(bsnap)


def _user_spec(spec_dir):
    fp.Spec(
        signature=fp.compute_signature(["E-mail", "Name", "Date"]),
        mappings=[
            fp.Mapping(target="email", source="E-mail", transform="strip_lower"),
            fp.Mapping(target="full_name", source="Name", transform="strip"),
            fp.Mapping(
                target="signup_date", source="Date", transform="parse_date:%d.%m.%Y"
            ),
        ],
    ).save(spec_dir)


# --------------------------------------------------------------------------- #
# registry / resolve
# --------------------------------------------------------------------------- #


def test_register_and_resolve_direct():
    register_batch_enrichment("noop", lambda recs: recs)
    assert "noop" in available_batch_enrichments()
    name, fn = resolve_batch_enrichment("noop")
    assert name == "noop"
    assert fn([{"a": 1}]) == [{"a": 1}]


def test_register_decorator_form():
    @register_batch_enrichment("deco")
    def enrich(records):
        return records

    assert "deco" in available_batch_enrichments()
    assert enrich([{"x": 1}]) == [{"x": 1}]


def test_register_duplicate_without_overwrite_raises():
    register_batch_enrichment("dup", lambda r: r)
    with pytest.raises(ValueError):
        register_batch_enrichment("dup", lambda r: r)


def test_resolve_unknown_name_raises():
    with pytest.raises(BatchEnrichmentError):
        resolve_batch_enrichment("nope")


def test_resolve_callable_passthrough():
    def my_batch(records):
        return records

    name, fn = resolve_batch_enrichment(my_batch)
    assert name == "my_batch" and fn is my_batch


def test_resolve_non_callable_raises():
    with pytest.raises(BatchEnrichmentError):
        resolve_batch_enrichment(123)


# --------------------------------------------------------------------------- #
# apply_batch_enrichment contract
# --------------------------------------------------------------------------- #


def test_apply_returns_same_length_list():
    out = apply_batch_enrichment(lambda recs: [{**r, "k": 1} for r in recs], [{"a": 0}, {"a": 1}])
    assert out == [{"a": 0, "k": 1}, {"a": 1, "k": 1}]


def test_apply_wrong_length_raises():
    with pytest.raises(BatchEnrichmentError):
        apply_batch_enrichment(lambda recs: recs[:-1], [{"a": 0}, {"a": 1}])


def test_apply_non_list_return_raises():
    with pytest.raises(BatchEnrichmentError):
        apply_batch_enrichment(lambda recs: {"not": "a list"}, [{"a": 0}])


def test_apply_non_dict_element_raises():
    with pytest.raises(BatchEnrichmentError):
        apply_batch_enrichment(lambda recs: ["nope"], [{"a": 0}])


# --------------------------------------------------------------------------- #
# End-to-end through the Parser
# --------------------------------------------------------------------------- #


class Account(BaseModel):
    email: str
    rank: int  # filled by a single batch pass, not present in the source


def test_batch_enrichment_fills_field_in_one_call(spec_dir):
    fp.Spec(
        signature=fp.compute_signature(["E-mail"]),
        mappings=[fp.Mapping(target="email", source="E-mail", transform="strip_lower")],
    ).save(spec_dir)

    calls = {"n": 0}

    @register_batch_enrichment("rank_all")
    def rank_all(records):
        # ONE call for the whole feed — assign rank by position.
        calls["n"] += 1
        for i, rec in enumerate(records):
            rec["rank"] = i
        return records

    parser = fp.Parser(Account, spec_store=spec_dir, llm=None, batch_enrich=["rank_all"])
    result = parser.parse([{"E-mail": "a@b.com"}, {"E-mail": "b@c.com"}, {"E-mail": "c@d.com"}])

    assert calls["n"] == 1  # not once-per-row
    assert result.errors == []
    assert [r.rank for r in result.valid_rows] == [0, 1, 2]


def test_batch_excludes_and_preserves_error_rows(spec_dir):
    """A row that fails mapping is not handed to the batcher, and is kept as an error."""

    _user_spec(spec_dir)

    seen_counts = []

    @register_batch_enrichment("count")
    def count(records):
        seen_counts.append(len(records))
        for rec in records:
            rec["full_name"] = rec["full_name"].upper()
        return records

    parser = fp.Parser(User, spec_store=spec_dir, llm=None, batch_enrich=["count"])
    result = parser.parse([
        {"E-mail": "a@b.com", "Name": "Alice", "Date": "01.02.2026"},
        {"E-mail": "bob@x.com", "Name": "Bob", "Date": "not-a-date"},  # bad -> error
    ])

    # Batcher saw only the 1 clean row; the bad row never reached it.
    assert seen_counts == [1]
    assert len(result.valid_rows) == 1
    assert result.valid_rows[0].full_name == "ALICE"
    assert len(result.errors) == 1 and result.errors[0].row_index == 1


def test_batch_runs_after_per_row_enrich(spec_dir):
    """Per-row enrichers run first; the batcher sees their output."""

    _user_spec(spec_dir)
    fp.register_enrichment("suffix", lambda r, s: {**r, "full_name": r["full_name"] + "-row"})

    @register_batch_enrichment("upper_all")
    def upper_all(records):
        for rec in records:
            rec["full_name"] = rec["full_name"].upper()
        return records

    parser = fp.Parser(
        User, spec_store=spec_dir, llm=None, enrich=["suffix"], batch_enrich=["upper_all"]
    )
    result = parser.parse([{"E-mail": "a@b.com", "Name": "Alice", "Date": "01.02.2026"}])
    assert result.valid_rows[0].full_name == "ALICE-ROW"


def test_batch_contract_violation_raises(spec_dir):
    _user_spec(spec_dir)

    @register_batch_enrichment("drops_a_row")
    def drops_a_row(records):
        return records[:-1]  # breaks one-to-one

    parser = fp.Parser(User, spec_store=spec_dir, llm=None, batch_enrich=["drops_a_row"])
    with pytest.raises(BatchEnrichmentError):
        parser.parse([
            {"E-mail": "a@b.com", "Name": "Alice", "Date": "01.02.2026"},
            {"E-mail": "b@c.com", "Name": "Bob", "Date": "15.03.2026"},
        ])


def test_unknown_batch_name_fails_at_construction(spec_dir):
    with pytest.raises(BatchEnrichmentError):
        fp.Parser(User, spec_store=spec_dir, llm=None, batch_enrich=["nope"])


def test_callable_batch_enricher_accepted(spec_dir):
    _user_spec(spec_dir)

    def tag_all(records):
        for rec in records:
            rec["full_name"] = "X"
        return records

    parser = fp.Parser(User, spec_store=spec_dir, llm=None, batch_enrich=[tag_all])
    result = parser.parse([{"E-mail": "a@b.com", "Name": "Alice", "Date": "01.02.2026"}])
    assert result.valid_rows[0].full_name == "X"
