"""Tests for declarative skip rows + clip transform (minor items)."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

import fidelis as fp
from fidelis import apply_transform
from fidelis.spec import Condition


# --------------------------------------------------------------------------- #
# clip transform
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "spec,value,expected",
    [
        ("clip:0:100", "150", 100.0),
        ("clip:0:100", "-5", 0.0),
        ("clip:0:100", "42", 42.0),
        ("clip:10:", "5", 10.0),     # lower bound only
        ("clip::100", "150", 100.0), # upper bound only
        ("clip:0:100", "1 234,5", 100.0),  # parses spaces/comma first
    ],
)
def test_clip(spec, value, expected):
    assert apply_transform(spec, value) == expected


# --------------------------------------------------------------------------- #
# skip_when
# --------------------------------------------------------------------------- #


class Order(BaseModel):
    sku: str
    status: str


def _spec(spec_dir, skip_when):
    fp.Spec(
        signature=fp.compute_signature(["SKU", "STATUS"]),
        mappings=[
            fp.Mapping(target="sku", source="SKU", transform="strip"),
            fp.Mapping(target="status", source="STATUS", transform="strip"),
        ],
        skip_when=skip_when,
    ).save(spec_dir)


def test_skip_when_stoplist(spec_dir):
    _spec(spec_dir, [Condition(field="STATUS", op="in", value=["CANCELLED", "VOID"])])
    result = fp.Parser(Order, spec_store=spec_dir, llm=None).parse([
        {"SKU": "A", "STATUS": "ACTIVE"},
        {"SKU": "B", "STATUS": "CANCELLED"},
        {"SKU": "C", "STATUS": "VOID"},
        {"SKU": "D", "STATUS": "ACTIVE"},
    ])
    assert {r.sku for r in result.valid_rows} == {"A", "D"}
    # Skipped rows are still counted in the coverage denominator.
    assert result.coverage.rows_in == 4
    assert result.coverage.rows_with_output == 2


def test_skip_when_empty(spec_dir):
    _spec(spec_dir, [Condition(field="STATUS", op="empty")])
    result = fp.Parser(Order, spec_store=spec_dir, llm=None).parse([
        {"SKU": "A", "STATUS": "ACTIVE"},
        {"SKU": "B", "STATUS": ""},
    ])
    assert {r.sku for r in result.valid_rows} == {"A"}


def test_skip_when_roundtrips_in_yaml(spec_dir):
    _spec(spec_dir, [Condition(field="STATUS", op="in", value=["X"])])
    spec = fp.Spec.load(next(spec_dir.glob("spec_*.yaml")))
    assert spec.skip_when[0].field == "STATUS"
    assert spec.skip_when[0].op == "in" and spec.skip_when[0].value == ["X"]
