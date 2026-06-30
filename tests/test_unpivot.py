"""Tests for column-group fan-out / unpivot (#2).

Repeating column groups (Q1_PRICE, Q1_QTY, Q2_PRICE, …) become one record per
group via a pre-mapping unpivot, then the normal mappings reference the canonical
names (PRICE, QTY) plus the index field.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

import fidelis as fp
from fidelis.mapping import unpivot_record
from fidelis.spec import UnpivotSpec


class Quote(BaseModel):
    quarter: int
    price: float
    qty: int


WIDE = ["Q1_PRICE", "Q1_QTY", "Q2_PRICE", "Q2_QTY", "Q3_PRICE", "Q3_QTY"]


def _spec(spec_dir, **unpivot_kw):
    fp.Spec(
        signature=fp.compute_signature(WIDE),
        unpivot=UnpivotSpec(
            columns={"PRICE": "Q{i}_PRICE", "QTY": "Q{i}_QTY"},
            index_field="quarter",
            **unpivot_kw,
        ),
        mappings=[
            fp.Mapping(target="quarter", source="quarter", transform="to_int"),
            fp.Mapping(target="price", source="PRICE", transform="to_float"),
            fp.Mapping(target="qty", source="QTY", transform="to_int"),
        ],
    ).save(spec_dir)


# --------------------------------------------------------------------------- #
# unpivot_record unit
# --------------------------------------------------------------------------- #


def test_unpivot_record_basic():
    u = UnpivotSpec(columns={"PRICE": "Q{i}_PRICE", "QTY": "Q{i}_QTY"}, count=3, index_field="quarter")
    rows = unpivot_record(u, {
        "Q1_PRICE": "10", "Q1_QTY": "1",
        "Q2_PRICE": "20", "Q2_QTY": "2",
        "Q3_PRICE": "30", "Q3_QTY": "3",
        "Region": "EU",
    })
    assert len(rows) == 3
    assert rows[0] == {"Region": "EU", "PRICE": "10", "QTY": "1", "quarter": 1}
    assert rows[2]["PRICE"] == "30" and rows[2]["quarter"] == 3


def test_unpivot_drops_empty_group():
    u = UnpivotSpec(columns={"PRICE": "Q{i}_PRICE"}, count=3, drop_empty=True)
    rows = unpivot_record(u, {"Q1_PRICE": "10", "Q2_PRICE": "", "Q3_PRICE": "30"})
    assert [r["PRICE"] for r in rows] == ["10", "30"]  # Q2 dropped


def test_unpivot_explicit_index():
    u = UnpivotSpec(columns={"P": "m{i}"}, index=["a", "b"])
    rows = unpivot_record(u, {"ma": "1", "mb": "2"})
    assert [r["P"] for r in rows] == ["1", "2"]


def test_unpivot_count_xor_index():
    with pytest.raises(ValidationError):
        UnpivotSpec(columns={"P": "m{i}"})  # neither
    with pytest.raises(ValidationError):
        UnpivotSpec(columns={"P": "m{i}"}, count=2, index=[1, 2])  # both


# --------------------------------------------------------------------------- #
# End-to-end
# --------------------------------------------------------------------------- #


def test_unpivot_one_row_becomes_three(spec_dir):
    _spec(spec_dir, count=3)
    result = fp.Parser(Quote, spec_store=spec_dir, llm=None).parse([{
        "Q1_PRICE": "10.0", "Q1_QTY": "1",
        "Q2_PRICE": "20.0", "Q2_QTY": "2",
        "Q3_PRICE": "30.0", "Q3_QTY": "3",
    }])
    assert result.errors == []
    assert len(result.valid_rows) == 3
    assert result.valid_rows[0] == Quote(quarter=1, price=10.0, qty=1)
    assert result.valid_rows[2] == Quote(quarter=3, price=30.0, qty=3)
    # Coverage counts the single source row.
    assert result.coverage.rows_in == 1 and result.coverage.records_out == 3


def test_unpivot_partial_row(spec_dir):
    _spec(spec_dir, count=3)
    # Only Q1 and Q3 present; Q2 empty -> 2 records.
    result = fp.Parser(Quote, spec_store=spec_dir, llm=None).parse([{
        "Q1_PRICE": "10.0", "Q1_QTY": "1",
        "Q2_PRICE": "", "Q2_QTY": "",
        "Q3_PRICE": "30.0", "Q3_QTY": "3",
    }])
    assert {r.quarter for r in result.valid_rows} == {1, 3}


def test_unpivot_roundtrips_in_yaml(spec_dir):
    _spec(spec_dir, count=3)
    spec = fp.Spec.load(next(spec_dir.glob("spec_*.yaml")))
    assert spec.unpivot.count == 3
    assert spec.unpivot.columns == {"PRICE": "Q{i}_PRICE", "QTY": "Q{i}_QTY"}
    assert spec.unpivot.index_field == "quarter"
