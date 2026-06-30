"""Tests for row expansion / fan-out (fidelis/expand.py + Parser wiring).

Two forms in the spec:
- declarative split: ``{field: airport_code, delimiter: "|"}`` (no code);
- custom expander:    ``{expander: name}`` (registered function).

Headline scenario: one source row lists several airports in a single cell; it
fans out into one record per airport, and enrichment then runs for EACH airport
independently.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

import fidelis as fp
from fidelis import enrichment as E
from fidelis import expand as X
from fidelis.expand import (
    ExpansionError,
    apply_expander,
    available_expanders,
    register_expander,
    resolve_expander,
    split_rows,
)
from fidelis.spec import ExpandStep


@pytest.fixture(autouse=True)
def _restore_registries():
    snap, bsnap, xsnap = dict(E._REGISTRY), dict(E._BATCH_REGISTRY), dict(X._REGISTRY)
    try:
        yield
    finally:
        for reg, s in ((E._REGISTRY, snap), (E._BATCH_REGISTRY, bsnap), (X._REGISTRY, xsnap)):
            reg.clear()
            reg.update(s)


# --------------------------------------------------------------------------- #
# ExpandStep model
# --------------------------------------------------------------------------- #


def test_expand_step_split_form():
    s = ExpandStep(field="airport_code", delimiter="|")
    assert s.field == "airport_code" and s.delimiter == "|" and s.expander is None


def test_expand_step_custom_form():
    s = ExpandStep(expander="by_region")
    assert s.expander == "by_region" and s.field is None


def test_expand_step_requires_exactly_one():
    with pytest.raises(ValidationError):
        ExpandStep()  # neither
    with pytest.raises(ValidationError):
        ExpandStep(field="a", expander="b")  # both


# --------------------------------------------------------------------------- #
# split_rows + apply_expander
# --------------------------------------------------------------------------- #


def test_split_rows_basic():
    ex = split_rows("airport", "|")
    assert ex({"airport": "JFK|LAX|ORD", "x": 1}, {}) == [
        {"airport": "JFK", "x": 1},
        {"airport": "LAX", "x": 1},
        {"airport": "ORD", "x": 1},
    ]


def test_split_rows_strips_and_drops_empty():
    ex = split_rows("a", ",")
    assert ex({"a": " X , , Y "}, {}) == [{"a": "X"}, {"a": "Y"}]


def test_split_rows_single_or_missing():
    ex = split_rows("a", "|")
    assert ex({"a": "JFK"}, {}) == [{"a": "JFK"}]
    assert ex({"b": 1}, {}) == [{"b": 1}]


def test_apply_expander_contract():
    with pytest.raises(ExpansionError):
        apply_expander(lambda r, s: ["not-a-dict"], {}, {})
    with pytest.raises(ExpansionError):
        apply_expander(lambda r, s: None, {}, {})


def test_register_and_resolve_custom():
    register_expander("dup", lambda r, s: [r, r])
    assert "dup" in available_expanders()
    name, fn = resolve_expander("dup")
    assert name == "dup" and fn({"a": 1}, {}) == [{"a": 1}, {"a": 1}]


def test_resolve_unknown_raises():
    with pytest.raises(ExpansionError):
        resolve_expander("nope")


# --------------------------------------------------------------------------- #
# YAML round-trip (both forms)
# --------------------------------------------------------------------------- #


def test_spec_roundtrips_split_form():
    spec = fp.Spec(
        signature="abc",
        mappings=[fp.Mapping(target="email", source="E-mail")],
        expand=[ExpandStep(field="airport_code", delimiter="|")],
    )
    text = spec.dump_yaml()
    assert "expand:" in text and "field: airport_code" in text and "delimiter:" in text
    back = fp.Spec.from_yaml(text)
    assert back.expand[0].field == "airport_code" and back.expand[0].delimiter == "|"


def test_spec_roundtrips_custom_form():
    spec = fp.Spec(
        signature="abc",
        mappings=[fp.Mapping(target="email", source="E-mail")],
        expand=[ExpandStep(expander="by_region")],
    )
    back = fp.Spec.from_yaml(spec.dump_yaml())
    assert back.expand[0].expander == "by_region"


# --------------------------------------------------------------------------- #
# End-to-end
# --------------------------------------------------------------------------- #


class Price(BaseModel):
    airport: str
    region: str
    price: float


_REGION = {"JFK": "US-East", "LAX": "US-West", "LHR": "EU"}


def _spec(spec_dir, **extra):
    fp.Spec(
        signature=fp.compute_signature(["Airports", "Price"]),
        mappings=[
            fp.Mapping(target="airport", source="Airports", transform="strip"),
            fp.Mapping(target="price", source="Price", transform="to_float"),
        ],
        **extra,
    ).save(spec_dir)


def test_declarative_split_fans_out_then_enriches_each(spec_dir):
    # No expander registered — the split is declarative. Enrichment runs PER
    # airport, deriving that airport's region.
    @fp.register_enrichment("attach_region")
    def attach_region(record, source):
        record["region"] = _REGION[record["airport"]]
        return record

    _spec(
        spec_dir,
        expand=[ExpandStep(field="airport", delimiter="|")],
        enrich=["attach_region"],
    )
    result = fp.Parser(Price, spec_store=spec_dir, llm=None).parse(
        [{"Airports": "JFK|LAX", "Price": "6.85"}]
    )
    assert result.errors == []
    assert result.valid_rows[0] == Price(airport="JFK", region="US-East", price=6.85)
    assert result.valid_rows[1] == Price(airport="LAX", region="US-West", price=6.85)


def test_custom_expander_form(spec_dir):
    @fp.register_expander("by_airport")
    def by_airport(record, source):
        # read a RAW column that isn't a target, split on a vendor-specific sep
        return [{**record, "airport": c.strip()} for c in source["Airports"].split("/")]

    @fp.register_enrichment("attach_region")
    def attach_region(record, source):
        record["region"] = _REGION[record["airport"]]
        return record

    _spec(spec_dir, expand=[ExpandStep(expander="by_airport")], enrich=["attach_region"])
    result = fp.Parser(Price, spec_store=spec_dir, llm=None).parse(
        [{"Airports": "JFK / LHR", "Price": "1.0"}]
    )
    assert {r.airport for r in result.valid_rows} == {"JFK", "LHR"}


def test_batch_enrich_runs_on_expanded_rows(spec_dir):
    seen = []

    @fp.register_batch_enrichment("collect")
    def collect(records):
        seen.append([r["airport"] for r in records])
        for r in records:
            r["region"] = _REGION[r["airport"]]
        return records

    _spec(spec_dir, expand=[ExpandStep(field="airport", delimiter="|")], batch_enrich=["collect"])
    result = fp.Parser(Price, spec_store=spec_dir, llm=None).parse(
        [{"Airports": "JFK|LHR", "Price": "1.0"}]
    )
    assert seen == [["JFK", "LHR"]]
    assert {r.airport for r in result.valid_rows} == {"JFK", "LHR"}


def test_one_fanned_row_errors_others_survive(spec_dir):
    @fp.register_enrichment("attach_region")
    def attach_region(record, source):
        record["region"] = _REGION[record["airport"]]  # KeyError on unknown
        return record

    _spec(spec_dir, expand=[ExpandStep(field="airport", delimiter="|")], enrich=["attach_region"])
    result = fp.Parser(Price, spec_store=spec_dir, llm=None).parse(
        [{"Airports": "JFK|ZZZ", "Price": "1.0"}]
    )
    assert len(result.valid_rows) == 1 and result.valid_rows[0].airport == "JFK"
    assert len(result.errors) == 1 and result.errors[0].row_index == 0


def test_parser_default_expand_when_spec_silent(spec_dir):
    _spec(spec_dir)  # no expand in the spec
    parser = fp.Parser(
        Price, spec_store=spec_dir, llm=None,
        expand=[split_rows("airport", "|")],
        enrich=[lambda r, s: {**r, "region": _REGION.get(r["airport"], "?")}],
    )
    result = parser.parse([{"Airports": "JFK|LAX", "Price": "2.0"}])
    assert len(result.valid_rows) == 2


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def test_validate_spec_flags_unknown_expander(spec_dir):
    _spec(spec_dir, expand=[ExpandStep(expander="ghost")])
    spec = fp.Spec.load(next(spec_dir.glob("spec_*.yaml")))
    problems = fp.Parser(Price, spec_store=spec_dir, llm=None).validate_spec(spec)
    assert any("ghost" in p and "expander" in p for p in problems)


def test_validate_spec_flags_bad_split_field(spec_dir):
    _spec(spec_dir, expand=[ExpandStep(field="not_a_field", delimiter="|")])
    spec = fp.Spec.load(next(spec_dir.glob("spec_*.yaml")))
    problems = fp.Parser(Price, spec_store=spec_dir, llm=None).validate_spec(spec)
    assert any("not_a_field" in p and "expand field" in p for p in problems)


def test_validate_spec_accepts_declarative_split(spec_dir):
    _spec(spec_dir, expand=[ExpandStep(field="airport", delimiter="|")])
    spec = fp.Spec.load(next(spec_dir.glob("spec_*.yaml")))
    problems = fp.Parser(Price, spec_store=spec_dir, llm=None).validate_spec(spec)
    # A valid declarative split raises no expand-related problem.
    assert not any("expand" in p or "expander" in p for p in problems)
