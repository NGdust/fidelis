"""Tests for conditional multi-record rules (#1).

One source row emits a record per firing rule (base mappings + the rule's
mappings). The headline case: RETAIL_PRICE and WHOLESALE_PRICE in one row become
two records, each only when its price is present.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

import fidelis as fp
from fidelis.mapping import _condition_matches, _normalized_lookup
from fidelis.spec import Condition, Rule


class Price(BaseModel):
    sku: str
    kind: str
    amount: float


def _spec(spec_dir):
    fp.Spec(
        signature=fp.compute_signature(["SKU", "RETAIL_PRICE", "WHOLESALE_PRICE"]),
        mappings=[fp.Mapping(target="sku", source="SKU", transform="strip")],  # shared
        rules=[
            Rule(
                when=Condition(field="RETAIL_PRICE", op="not_empty"),
                mappings=[
                    fp.Mapping(target="kind", value="retail"),
                    fp.Mapping(target="amount", source="RETAIL_PRICE", transform="to_float"),
                ],
            ),
            Rule(
                when=Condition(field="WHOLESALE_PRICE", op="not_empty"),
                mappings=[
                    fp.Mapping(target="kind", value="wholesale"),
                    fp.Mapping(target="amount", source="WHOLESALE_PRICE", transform="to_float"),
                ],
            ),
        ],
    ).save(spec_dir)


# --------------------------------------------------------------------------- #
# condition evaluation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "op,value,cell,expected",
    [
        ("not_empty", None, "x", True),
        ("not_empty", None, "", False),
        ("empty", None, "", True),
        ("eq", "R", "R", True),
        ("eq", "R", "W", False),
        ("ne", "R", "W", True),
        ("in", ["A", "B"], "B", True),
        ("in", ["A", "B"], "C", False),
        ("gt", 0, "5", True),
        ("gt", 10, "5", False),
        ("le", 5, "5", True),
    ],
)
def test_condition_matches(op, value, cell, expected):
    cond = Condition(field="F", op=op, value=value)
    assert _condition_matches(cond, _normalized_lookup({"F": cell})) is expected


# --------------------------------------------------------------------------- #
# end-to-end
# --------------------------------------------------------------------------- #


def test_both_rules_fire_two_records(spec_dir):
    _spec(spec_dir)
    result = fp.Parser(Price, spec_store=spec_dir, llm=None).parse([
        {"SKU": "A-1", "RETAIL_PRICE": "9.99", "WHOLESALE_PRICE": "7.50"},
    ])
    assert result.errors == []
    kinds = {(r.kind, r.amount) for r in result.valid_rows}
    assert kinds == {("retail", 9.99), ("wholesale", 7.50)}
    assert all(r.sku == "A-1" for r in result.valid_rows)  # shared base mapping


def test_only_one_rule_fires(spec_dir):
    _spec(spec_dir)
    result = fp.Parser(Price, spec_store=spec_dir, llm=None).parse([
        {"SKU": "A-2", "RETAIL_PRICE": "9.99", "WHOLESALE_PRICE": ""},
    ])
    assert len(result.valid_rows) == 1
    assert result.valid_rows[0].kind == "retail"


def test_no_rule_fires_no_record(spec_dir):
    _spec(spec_dir)
    result = fp.Parser(Price, spec_store=spec_dir, llm=None).parse([
        {"SKU": "A-3", "RETAIL_PRICE": "", "WHOLESALE_PRICE": ""},
    ])
    assert result.valid_rows == []
    # The source row produced nothing, so coverage reflects it.
    assert result.coverage.rows_in == 1 and result.coverage.rows_with_output == 0


def test_rules_roundtrip_in_yaml(spec_dir):
    _spec(spec_dir)
    spec = fp.Spec.load(next(spec_dir.glob("spec_*.yaml")))
    assert len(spec.rules) == 2
    assert spec.rules[0].when.field == "RETAIL_PRICE"
    assert spec.rules[0].mappings[0].value == "retail"


def test_validate_spec_flags_bad_rule_target(spec_dir):
    fp.Spec(
        signature="abc",
        mappings=[fp.Mapping(target="sku", source="SKU")],
        rules=[Rule(when=Condition(field="X"), mappings=[fp.Mapping(target="nope", value="x")])],
    ).save(spec_dir)
    spec = fp.Spec.load(next(spec_dir.glob("spec_*.yaml")))
    problems = fp.Parser(Price, spec_store=spec_dir, llm=None).validate_spec(spec)
    assert any("nope" in p and "rule target" in p for p in problems)
