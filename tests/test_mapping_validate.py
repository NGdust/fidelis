"""Tests for fidelis/mapping.py and fidelis/validate.py.

Coverage:
  * map_record resolves source fields by NORMALIZED name (CSV/JSON-agnostic);
  * a transform failure yields MappedRow.error (RowError) and is not silently lost;
  * a missing source field maps to None;
  * validate_rows splits valid/errors, the error carries row_index/field/reason;
  * strict=True raises on the first error (transform OR validation);
  * resolve_model accepts a class and a dotted path, rejects non-BaseModel.

Each test is independent (nothing shares state between tests).
"""

from __future__ import annotations

from datetime import date

import pytest

from conftest import USER_MAPPINGS, Product, User
from fidelis.mapping import MappedRow, map_record, map_records
from fidelis.result import RowError
from fidelis.spec import Mapping, Spec
from fidelis.validate import resolve_model, validate_rows


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def make_spec(mappings: list[dict]) -> Spec:
    """Build a minimal valid Spec from a list of dict mappings."""

    return Spec(
        signature="abc123",
        mappings=[Mapping(**m) for m in mappings],
    )


# --------------------------------------------------------------------------- #
# map_record: resolution by normalized name
# --------------------------------------------------------------------------- #


def test_map_record_resolves_case_insensitively():
    """Spec source 'E-mail' matches the record key 'E-MAIL' (case-insensitive)."""

    spec = make_spec(
        [{"source": "E-mail", "target": "email", "transform": "strip_lower"}]
    )
    row = map_record(spec, {"E-MAIL": " A@B.COM "}, 0)

    assert row.error is None
    assert row.data["email"] == "a@b.com"


def test_map_record_resolves_underscore_vs_space():
    """Spec source 'Full Name' matches the key 'full_name' (underscore ~ space)."""

    spec = make_spec(
        [{"source": "Full Name", "target": "full_name", "transform": "strip"}],
    )
    row = map_record(spec, {"full_name": "  Alice  "}, 0)

    assert row.error is None
    assert row.data["full_name"] == "Alice"


def test_map_record_resolves_spec_underscore_to_record_space():
    """Symmetric: spec source 'full_name' matches the key 'Full Name'."""

    spec = make_spec(
        [{"source": "full_name", "target": "full_name", "transform": "strip"}],
    )
    row = map_record(spec, {"Full Name": " Bob "}, 0)

    assert row.error is None
    assert row.data["full_name"] == "Bob"


def test_map_record_does_not_treat_hyphen_as_underscore():
    """PRD §8.1: normalization collapses only spaces/underscores, NOT hyphens.

    So source 'E-mail' and key 'e_mail' are DIFFERENT fields by contract. There is
    no match → target maps to None (the field did not resolve), but it is not
    silently lost: the missing value is caught by Pydantic validation, not by a
    silent row drop.
    """

    spec = make_spec(
        [{"source": "E-mail", "target": "email", "transform": "strip_lower"}]
    )
    row = map_record(spec, {"e_mail": " A@B.COM "}, 0)

    assert row.error is None
    assert row.data["email"] is None


def test_map_record_uses_normalized_lookup_not_exact_key():
    """Matching is by normalized name, not by the exact string key."""

    spec = make_spec(
        [{"source": "  Name  ", "target": "full_name", "transform": "strip"}],
    )
    # The record key differs in surrounding whitespace and case.
    row = map_record(spec, {"NAME": " Carol "}, 0)

    assert row.error is None
    assert row.data["full_name"] == "Carol"


# --------------------------------------------------------------------------- #
# map_record: transform failure -> error, not lost
# --------------------------------------------------------------------------- #


def test_transform_failure_sets_error_with_field_and_raw_value():
    """A transform failure stores a RowError with the right field/raw_value/row_index."""

    spec = make_spec(
        [{"source": "Date", "target": "signup_date", "transform": "parse_date:%d.%m.%Y"}]
    )
    row = map_record(spec, {"Date": "not-a-date"}, 5)

    assert isinstance(row, MappedRow)
    assert row.error is not None
    assert isinstance(row.error, RowError)
    assert row.error.field == "signup_date"
    assert row.error.raw_value == "not-a-date"
    assert row.error.row_index == 5
    assert "transform" in row.error.reason


def test_transform_failure_is_not_silently_dropped():
    """A record with a transform error is still returned (MappedRow), not lost."""

    spec = make_spec(
        [{"source": "Price", "target": "price", "transform": "to_float"}],
    )
    rows = list(map_records(spec, [{"Price": "abc"}]))

    assert len(rows) == 1
    assert rows[0].error is not None
    assert rows[0].error.field == "price"
    assert rows[0].error.raw_value == "abc"


def test_transform_failure_stops_at_failing_mapping():
    """On a transform error, a partially filled data and the right field are returned."""

    spec = make_spec(
        [
            {"source": "Name", "target": "full_name", "transform": "strip"},
            {"source": "Date", "target": "signup_date", "transform": "parse_date:%d.%m.%Y"},
        ]
    )
    row = map_record(spec, {"Name": " Dave ", "Date": "33.99.2026"}, 2)

    assert row.error is not None
    # The field that actually failed.
    assert row.error.field == "signup_date"
    # The previous successful mapping is already in data.
    assert row.data.get("full_name") == "Dave"


# --------------------------------------------------------------------------- #
# map_record: missing field -> None
# --------------------------------------------------------------------------- #


def test_missing_source_field_maps_to_none():
    """A source field missing from the record yields None in data, not an error."""

    spec = make_spec(
        [{"source": "Phone", "target": "email", "transform": "strip_lower"}]
    )
    row = map_record(spec, {"Name": "Eve"}, 0)

    assert row.error is None
    assert "email" in row.data
    assert row.data["email"] is None


def test_missing_source_field_without_transform_maps_to_none():
    """Without a transform, a missing field is also None (the value passes through)."""

    spec = make_spec(
        [{"source": "Missing", "target": "full_name", "transform": None}]
    )
    row = map_record(spec, {}, 0)

    assert row.error is None
    assert row.data["full_name"] is None


# --------------------------------------------------------------------------- #
# map_records: stream indexing
# --------------------------------------------------------------------------- #


def test_map_records_assigns_increasing_row_indices():
    """map_records numbers records 0..N-1 in order."""

    spec = make_spec(
        [{"source": "Name", "target": "full_name", "transform": "strip"}]
    )
    rows = list(map_records(spec, [{"Name": "a"}, {"Name": "b"}, {"Name": "c"}]))

    assert [r.row_index for r in rows] == [0, 1, 2]
    assert [r.data["full_name"] for r in rows] == ["a", "b", "c"]


# --------------------------------------------------------------------------- #
# validate_rows: split valid/errors
# --------------------------------------------------------------------------- #


def test_validate_rows_splits_valid_and_errors():
    """validate_rows splits records that passed from those that failed."""

    good = MappedRow(
        row_index=0,
        data={"email": "a@b.com", "full_name": "Alice", "signup_date": date(2026, 2, 1)},
    )
    bad = MappedRow(
        row_index=1,
        data={"email": "x@y.com", "full_name": "Bob"},  # no signup_date
    )
    valid, errors, _cov = validate_rows(User, [good, bad])

    assert len(valid) == 1
    assert isinstance(valid[0], User)
    assert valid[0].full_name == "Alice"
    assert len(errors) == 1
    assert errors[0].row_index == 1


def test_validate_error_carries_row_index_field_and_reason():
    """A RowError from ValidationError carries row_index/field/reason/raw_value."""

    bad = MappedRow(
        row_index=7,
        data={"email": "a@b.com", "full_name": "Bob", "signup_date": "garbage"},
    )
    valid, errors, _cov = validate_rows(User, [bad])

    assert valid == []
    assert len(errors) == 1
    err = errors[0]
    assert isinstance(err, RowError)
    assert err.row_index == 7
    assert err.field == "signup_date"
    assert err.raw_value == "garbage"
    assert err.reason  # non-empty reason from pydantic


def test_validate_rows_carries_transform_error_through():
    """An already-set MappedRow.error ends up in errors as-is (non-strict)."""

    pre = RowError(row_index=3, field="price", raw_value="abc", reason="transform failed")
    mapped = MappedRow(row_index=3, data={}, error=pre)

    valid, errors, _cov = validate_rows(Product, [mapped])

    assert valid == []
    assert errors == [pre]


def test_validate_rows_preserves_per_field_errors():
    """Several problematic fields yield several RowErrors with the right field."""

    bad = MappedRow(
        row_index=0,
        data={"email": "a@b.com"},  # no full_name and signup_date
    )
    valid, errors, _cov = validate_rows(User, [bad])

    assert valid == []
    fields = {e.field for e in errors}
    assert "full_name" in fields
    assert "signup_date" in fields
    assert all(e.row_index == 0 for e in errors)


# --------------------------------------------------------------------------- #
# validate_rows: strict=True
# --------------------------------------------------------------------------- #


def test_strict_raises_on_validation_error():
    """strict=True raises on the first validation error."""

    bad = MappedRow(
        row_index=0,
        data={"email": "a@b.com", "full_name": "Bob"},  # no signup_date
    )
    with pytest.raises(ValueError):
        validate_rows(User, [bad], strict=True)


def test_strict_raises_on_transform_error():
    """strict=True also raises on a transform error (MappedRow.error)."""

    pre = RowError(row_index=0, field="price", raw_value="abc", reason="transform failed")
    mapped = MappedRow(row_index=0, data={}, error=pre)
    with pytest.raises(ValueError):
        validate_rows(Product, [mapped], strict=True)


def test_strict_raises_before_processing_later_rows():
    """strict=True fails on the first error, never reaching later valid rows."""

    bad = MappedRow(row_index=0, data={"email": "a@b.com", "full_name": "B"})  # no date
    good = MappedRow(
        row_index=1,
        data={"email": "c@d.com", "full_name": "C", "signup_date": date(2026, 1, 1)},
    )

    def gen():
        yield bad
        yield good
        raise AssertionError("strict must not reach this point")

    with pytest.raises(ValueError):
        validate_rows(User, gen(), strict=True)


def test_nonstrict_does_not_raise_and_collects():
    """strict=False raises nothing and collects everything into errors."""

    bad = MappedRow(row_index=0, data={"email": "a@b.com", "full_name": "B"})
    valid, errors, _cov = validate_rows(User, [bad], strict=False)

    assert valid == []
    assert len(errors) >= 1


# --------------------------------------------------------------------------- #
# resolve_model
# --------------------------------------------------------------------------- #


def test_resolve_model_accepts_class():
    """resolve_model returns the passed model class as-is."""

    assert resolve_model(User) is User


def test_resolve_model_accepts_dotted_path():
    """resolve_model resolves a dotted-path string into a model class."""

    assert resolve_model("conftest.User") is User
    assert resolve_model("conftest.Product") is Product


def test_resolve_model_rejects_dotted_path_to_non_basemodel():
    """A dotted path pointing to a non-BaseModel is rejected with TypeError."""

    with pytest.raises(TypeError):
        resolve_model("conftest.USER_MAPPINGS")  # this is a list, not a model


def test_resolve_model_rejects_dotted_path_without_module():
    """A bare name without a module -> ValueError (invalid path)."""

    with pytest.raises(ValueError):
        resolve_model("NoModuleHere")


def test_resolve_model_rejects_non_basemodel_class():
    """A class not inheriting from BaseModel is rejected with TypeError."""

    class Plain:
        pass

    with pytest.raises(TypeError):
        resolve_model(Plain)


def test_resolve_model_rejects_unsupported_value():
    """Neither a class nor a string -> TypeError."""

    with pytest.raises(TypeError):
        resolve_model(123)
