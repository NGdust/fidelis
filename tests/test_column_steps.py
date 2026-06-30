"""Tests for the whole-column stage (#4): column_steps."""

from __future__ import annotations

import statistics

import pytest
from pydantic import BaseModel

import fidelis as fp
from fidelis import column as C
from fidelis.column import ColumnStepError, apply_column_step


@pytest.fixture(autouse=True)
def _restore():
    snap = dict(C._REGISTRY)
    try:
        yield
    finally:
        C._REGISTRY.clear()
        C._REGISTRY.update(snap)


class Item(BaseModel):
    price: float


def _spec(spec_dir):
    fp.Spec(
        signature=fp.compute_signature(["PRICE"]),
        mappings=[fp.Mapping(target="price", source="PRICE", transform="to_float")],
        column_steps={"price": "cents_if_huge"},
    ).save(spec_dir)


# --------------------------------------------------------------------------- #
# contract
# --------------------------------------------------------------------------- #


def test_apply_column_step_wrong_length_raises():
    with pytest.raises(ColumnStepError):
        apply_column_step(lambda values: values[:-1], [1, 2, 3])


def test_apply_column_step_non_list_raises():
    with pytest.raises(ColumnStepError):
        apply_column_step(lambda values: "nope", [1, 2])


# --------------------------------------------------------------------------- #
# end-to-end: median heuristic over the whole column
# --------------------------------------------------------------------------- #


def test_median_heuristic_rewrites_whole_column(spec_dir):
    @fp.register_column_step("cents_if_huge")
    def cents_if_huge(values):
        nums = [v for v in values if isinstance(v, (int, float))]
        if nums and statistics.median(nums) > 1000:        # looks like cents
            return [v / 100 if isinstance(v, (int, float)) else v for v in values]
        return values

    _spec(spec_dir)
    # Median is huge -> the whole column is divided by 100.
    result = fp.Parser(Item, spec_store=spec_dir, llm=None).parse([
        {"PRICE": "1999"}, {"PRICE": "2499"}, {"PRICE": "3050"},
    ])
    assert result.errors == []
    assert [r.price for r in result.valid_rows] == [19.99, 24.99, 30.5]


def test_column_step_left_alone_when_heuristic_false(spec_dir):
    @fp.register_column_step("cents_if_huge")
    def cents_if_huge(values):
        nums = [v for v in values if isinstance(v, (int, float))]
        if nums and statistics.median(nums) > 1000:
            return [v / 100 for v in values]
        return values

    _spec(spec_dir)
    result = fp.Parser(Item, spec_store=spec_dir, llm=None).parse([
        {"PRICE": "19.99"}, {"PRICE": "24.99"},
    ])
    assert [r.price for r in result.valid_rows] == [19.99, 24.99]  # unchanged


def test_column_step_receives_context(spec_dir):
    @fp.register_column_step("scale")
    def scale(values, context):
        factor = context["factor"]
        return [v * factor for v in values]

    fp.Spec(
        signature=fp.compute_signature(["PRICE"]),
        mappings=[fp.Mapping(target="price", source="PRICE", transform="to_float")],
        column_steps={"price": "scale"},
    ).save(spec_dir)

    parser = fp.Parser(Item, spec_store=spec_dir, llm=None, context={"factor": 0.5})
    result = parser.parse([{"PRICE": "10"}, {"PRICE": "20"}])
    assert [r.price for r in result.valid_rows] == [5.0, 10.0]


def test_validate_spec_flags_unknown_column_step(spec_dir):
    fp.Spec(
        signature="abc",
        mappings=[fp.Mapping(target="price", source="PRICE", transform="to_float")],
        column_steps={"price": "ghost"},
    ).save(spec_dir)
    spec = fp.Spec.load(next(spec_dir.glob("spec_*.yaml")))
    problems = fp.Parser(Item, spec_store=spec_dir, llm=None).validate_spec(spec)
    assert any("ghost" in p and "column step" in p for p in problems)


def test_column_steps_roundtrip_in_yaml(spec_dir):
    fp.register_column_step("cents_if_huge", lambda v: v)
    _spec(spec_dir)
    spec = fp.Spec.load(next(spec_dir.glob("spec_*.yaml")))
    assert spec.column_steps == {"price": "cents_if_huge"}
