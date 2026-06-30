"""Tests for two mapping capabilities:

A. `value` on a Mapping — a constant (no source) or a default (source empty).
B. enrichers receiving the original source record, so they can reach raw columns
   that aren't mapped to any target (combine / split / coalesce in code, declared
   in the spec).
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import BaseModel

import fidelis as fp
from fidelis import enrichment as E

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


# --------------------------------------------------------------------------- #
# A. value: constant / default
# --------------------------------------------------------------------------- #


class Tagged(BaseModel):
    email: str
    source_system: str          # constant, no source column
    country: str                # default when the source cell is empty


def _tagged_spec(spec_dir):
    fp.Spec(
        signature=fp.compute_signature(["E-mail", "Country"]),
        mappings=[
            fp.Mapping(target="email", source="E-mail", transform="strip_lower"),
            fp.Mapping(target="source_system", value="acme"),          # constant
            fp.Mapping(target="country", source="Country", value="US"),  # default
        ],
    ).save(spec_dir)


def test_constant_value_needs_no_source(spec_dir):
    _tagged_spec(spec_dir)
    result = fp.Parser(Tagged, spec_store=spec_dir, llm=None).parse(
        [{"E-mail": "a@b.com", "Country": "DE"}]
    )
    assert result.errors == []
    row = result.valid_rows[0]
    assert row.source_system == "acme"   # constant injected
    assert row.country == "DE"           # source present → used


def test_value_as_default_when_source_empty(spec_dir):
    _tagged_spec(spec_dir)
    result = fp.Parser(Tagged, spec_store=spec_dir, llm=None).parse(
        [{"E-mail": "a@b.com", "Country": ""}]   # empty → default
    )
    assert result.valid_rows[0].country == "US"


def test_value_default_when_source_missing(spec_dir):
    _tagged_spec(spec_dir)
    # Country column absent entirely → still defaulted.
    fp.Spec(
        signature=fp.compute_signature(["E-mail"]),
        mappings=[
            fp.Mapping(target="email", source="E-mail", transform="strip_lower"),
            fp.Mapping(target="source_system", value="acme"),
            fp.Mapping(target="country", source="Country", value="US"),
        ],
    ).save(spec_dir)
    result = fp.Parser(Tagged, spec_store=spec_dir, llm=None).parse([{"E-mail": "a@b.com"}])
    assert result.valid_rows[0].country == "US"


def test_value_roundtrips_in_yaml():
    spec = fp.Spec(
        signature="abc123",
        mappings=[fp.Mapping(target="full_name", value="N/A")],
    )
    text = spec.dump_yaml()
    assert "value: N/A" in text
    # No `source:` key emitted for a pure constant.
    back = fp.Spec.from_yaml(text)
    m = back.mappings[0]
    assert m.source is None and m.value == "N/A"


def test_validate_spec_constant_mapping_is_ok(spec_dir):
    _tagged_spec(spec_dir)
    spec = fp.Spec.load(next(spec_dir.glob("spec_*.yaml")))
    problems = fp.Parser(Tagged, spec_store=spec_dir, llm=None).validate_spec(spec)
    assert problems == []


def test_validate_spec_flags_mapping_without_source_or_value(spec_dir):
    spec = fp.Spec(
        signature="abc",
        mappings=[fp.Mapping(target="email")],  # neither source nor value
    )
    problems = fp.Parser(Tagged, spec_store=spec_dir, llm=None).validate_spec(spec)
    assert any("no source and no value" in p for p in problems)


# --------------------------------------------------------------------------- #
# B. enricher sees the raw source record
# --------------------------------------------------------------------------- #


def test_enricher_reaches_unmapped_source_column(spec_dir):
    """Combine two RAW columns that aren't mapped to any target."""

    # Only `email` is mapped; First/Last Name are NOT targets, but the enricher
    # reaches them via the source record to build full_name.
    fp.Spec(
        signature=fp.compute_signature(["E-mail", "First Name", "Last Name"]),
        mappings=[
            fp.Mapping(target="email", source="E-mail", transform="strip_lower"),
            fp.Mapping(target="signup_date", value="2026-01-01"),  # constant date
        ],
        enrich=["full_name_from_parts"],
    ).save(spec_dir)

    @fp.register_enrichment("full_name_from_parts")
    def combine(record, source):
        record["full_name"] = f"{source['First Name']} {source['Last Name']}".strip()
        return record

    result = fp.Parser(User, spec_store=spec_dir, llm=None).parse(
        [{"E-mail": "a@b.com", "First Name": "Alice", "Last Name": "Anderson"}]
    )
    assert result.errors == []
    assert result.valid_rows[0] == User(
        email="a@b.com", full_name="Alice Anderson", signup_date=date(2026, 1, 1)
    )


def test_enricher_split_one_source_into_two_targets(spec_dir):
    class Person(BaseModel):
        first: str
        last: str

    fp.Spec(
        signature=fp.compute_signature(["Full Name"]),
        mappings=[fp.Mapping(target="first", value=""), fp.Mapping(target="last", value="")],
        enrich=["split_name"],
    ).save(spec_dir)

    @fp.register_enrichment("split_name")
    def split_name(record, source):
        first, _, last = source["Full Name"].partition(" ")
        record["first"], record["last"] = first, last
        return record

    result = fp.Parser(Person, spec_store=spec_dir, llm=None).parse([{"Full Name": "Alice Anderson"}])
    assert result.valid_rows[0] == Person(first="Alice", last="Anderson")
