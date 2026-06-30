"""Tests for multi-target transforms (#3): one source → several model fields.

A transform returns a dict; the mapping's ``targets`` maps its keys to model
fields — e.g. keep the converted value *and* the original.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

import fidelis as fp
from fidelis import transforms as T
from fidelis.spec import Mapping


@pytest.fixture(autouse=True)
def _restore_transforms():
    snap = dict(T._REGISTRY)
    try:
        yield
    finally:
        T._REGISTRY.clear()
        T._REGISTRY.update(snap)


class Weighed(BaseModel):
    weight_kg: float
    weight_original: str
    weight_unit: str


def _spec(spec_dir):
    fp.Spec(
        signature=fp.compute_signature(["WEIGHT"]),
        mappings=[
            fp.Mapping(
                source="WEIGHT",
                transform="lbs_to_kg",
                targets={"kg": "weight_kg", "original": "weight_original", "unit": "weight_unit"},
            )
        ],
    ).save(spec_dir)


def test_multi_target_fills_several_fields(spec_dir):
    fp.register_transform(
        "lbs_to_kg",
        lambda value, arg: {
            "kg": round(float(value) * 0.453592, 3),
            "original": str(value),
            "unit": "lb",
        },
    )
    _spec(spec_dir)
    result = fp.Parser(Weighed, spec_store=spec_dir, llm=None).parse([{"WEIGHT": "10"}])
    assert result.errors == []
    row = result.valid_rows[0]
    assert row.weight_kg == 4.536
    assert row.weight_original == "10" and row.weight_unit == "lb"


def test_multi_target_non_dict_return_is_error(spec_dir):
    fp.register_transform("lbs_to_kg", lambda value, arg: 42)  # not a dict
    _spec(spec_dir)
    result = fp.Parser(Weighed, spec_store=spec_dir, llm=None).parse([{"WEIGHT": "10"}])
    assert result.valid_rows == []
    assert len(result.errors) == 1
    assert "must return a dict" in result.errors[0].reason


def test_multi_target_roundtrips_in_yaml():
    spec = fp.Spec(
        signature="abc",
        mappings=[fp.Mapping(source="W", transform="t", targets={"a": "weight_kg"})],
    )
    text = spec.dump_yaml()
    assert "targets:" in text and "weight_kg" in text
    back = fp.Spec.from_yaml(text)
    assert back.mappings[0].targets == {"a": "weight_kg"}
    assert back.mappings[0].target is None


def test_mapping_requires_exactly_one_of_target_targets():
    with pytest.raises(ValidationError):
        Mapping(source="W", transform="t")  # neither target nor targets
    with pytest.raises(ValidationError):
        Mapping(target="x", targets={"a": "b"}, source="W", transform="t")  # both


def test_multi_target_needs_source_and_transform():
    with pytest.raises(ValidationError):
        Mapping(targets={"a": "b"})  # missing source + transform


def test_validate_spec_flags_unknown_multi_target_field(spec_dir):
    fp.register_transform("lbs_to_kg", lambda v, a: {})
    fp.Spec(
        signature=fp.compute_signature(["WEIGHT"]),
        mappings=[fp.Mapping(source="WEIGHT", transform="lbs_to_kg", targets={"x": "not_a_field"})],
    ).save(spec_dir)
    spec = fp.Spec.load(next(spec_dir.glob("spec_*.yaml")))
    problems = fp.Parser(Weighed, spec_store=spec_dir, llm=None).validate_spec(spec)
    assert any("not_a_field" in p for p in problems)
