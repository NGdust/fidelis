"""Tests for fidelis/transforms.py.

Covers every builtin transform plus the registry/spec helpers. All tests are
independent; an autouse fixture snapshots and restores the transform registry
so custom registrations cannot leak between tests.
"""

from datetime import date, datetime

import pytest

from fidelis import transforms as T
from fidelis.transforms import (
    TransformError,
    apply_transform,
    available_transforms,
    parse_transform_spec,
    register_transform,
)


@pytest.fixture(autouse=True)
def _restore_registry():
    """Keep the global registry isolated per test."""
    snapshot = dict(T._REGISTRY)
    try:
        yield
    finally:
        T._REGISTRY.clear()
        T._REGISTRY.update(snapshot)


# --------------------------------------------------------------------------- #
# strip / strip_lower
# --------------------------------------------------------------------------- #


def test_strip_removes_surrounding_whitespace():
    assert apply_transform("strip", "  hello  ") == "hello"


def test_strip_passes_non_string_unchanged():
    # value is numeric -> strip is a no-op (returns as-is)
    assert apply_transform("strip", 42) == 42


def test_strip_lower_lowercases_and_strips():
    assert apply_transform("strip_lower", "  HeLLo  ") == "hello"


def test_strip_lower_non_string_unchanged():
    assert apply_transform("strip_lower", 7) == 7


# --------------------------------------------------------------------------- #
# to_int
# --------------------------------------------------------------------------- #


def test_to_int_from_plain_string():
    assert apply_transform("to_int", "123") == 123


def test_to_int_strips_regular_spaces():
    assert apply_transform("to_int", "1 000") == 1000


def test_to_int_strips_nbsp_thousands():
    # non-breaking space U+00A0 used as a thousands separator
    assert apply_transform("to_int", "1 234 567") == 1234567


def test_to_int_from_int_passthrough():
    assert apply_transform("to_int", 55) == 55


def test_to_int_from_float_truncates():
    assert apply_transform("to_int", 12.9) == 12


def test_to_int_from_bool():
    assert apply_transform("to_int", True) == 1
    assert apply_transform("to_int", False) == 0


def test_to_int_from_float_like_string():
    # "12.0" falls back through int(float(text))
    assert apply_transform("to_int", "12.0") == 12


def test_to_int_garbage_raises():
    with pytest.raises(ValueError):
        apply_transform("to_int", "abc")


# --------------------------------------------------------------------------- #
# to_float
# --------------------------------------------------------------------------- #


def test_to_float_from_plain_string():
    assert apply_transform("to_float", "3.14") == 3.14


def test_to_float_comma_decimal_separator():
    assert apply_transform("to_float", "3,14") == 3.14


def test_to_float_spaces_and_comma():
    assert apply_transform("to_float", "1 234,56") == 1234.56


def test_to_float_nbsp_and_comma():
    assert apply_transform("to_float", "1 234,56") == 1234.56


def test_to_float_from_int():
    result = apply_transform("to_float", 5)
    assert result == 5.0
    assert isinstance(result, float)


def test_to_float_from_float_passthrough():
    assert apply_transform("to_float", 2.5) == 2.5


def test_to_float_garbage_raises():
    with pytest.raises(ValueError):
        apply_transform("to_float", "not-a-number")


# --------------------------------------------------------------------------- #
# to_bool
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw",
    ["true", "1", "yes", "y", "yes", "true", "t", "TRUE", "  Yes  "],
)
def test_to_bool_truthy(raw):
    assert apply_transform("to_bool", raw) is True


@pytest.mark.parametrize(
    "raw",
    ["false", "0", "no", "n", "no", "false", "f", "FALSE", "  No  "],
)
def test_to_bool_falsy(raw):
    assert apply_transform("to_bool", raw) is False


def test_to_bool_bool_passthrough():
    assert apply_transform("to_bool", True) is True
    assert apply_transform("to_bool", False) is False


def test_to_bool_garbage_raises_transform_error():
    with pytest.raises(TransformError):
        apply_transform("to_bool", "maybe")


def test_to_bool_empty_string_via_registry_is_false():
    # apply_transform short-circuits "" to passthrough, so call the builtin
    # directly to exercise the "" -> False branch.
    assert T._REGISTRY["to_bool"]("", None) is False


# --------------------------------------------------------------------------- #
# parse_date
# --------------------------------------------------------------------------- #


def test_parse_date_with_explicit_format():
    assert apply_transform("parse_date:%d.%m.%Y", "28.06.2026") == date(2026, 6, 28)


def test_parse_date_iso_fallback():
    assert apply_transform("parse_date", "2026-06-28") == date(2026, 6, 28)


def test_parse_date_explicit_format_failure():
    with pytest.raises(ValueError):
        apply_transform("parse_date:%d.%m.%Y", "2026-06-28")


def test_parse_date_iso_failure():
    with pytest.raises(ValueError):
        apply_transform("parse_date", "not-a-date")


def test_parse_date_tries_multiple_formats_in_order():
    spec = "parse_date:%Y-%m-%d|%d/%m/%Y|%d.%m.%Y"
    assert apply_transform(spec, "2026-06-28") == date(2026, 6, 28)  # 1st format
    assert apply_transform(spec, "28/06/2026") == date(2026, 6, 28)  # 2nd format
    assert apply_transform(spec, "28.06.2026") == date(2026, 6, 28)  # 3rd format


def test_parse_date_multi_format_none_match_raises():
    with pytest.raises(TransformError):
        apply_transform("parse_date:%Y-%m-%d|%d/%m/%Y", "June 28 2026")


def test_parse_date_format_with_time_colon_still_works():
    # parse_transform_spec splits on the first colon only, so %H:%M survives.
    assert apply_transform("parse_date:%Y-%m-%d %H:%M", "2026-06-28 13:45") == date(2026, 6, 28)


def test_parse_date_from_datetime():
    assert apply_transform("parse_date", datetime(2026, 6, 28, 13, 45)) == date(2026, 6, 28)


def test_parse_date_from_date_passthrough():
    d = date(2026, 6, 28)
    assert apply_transform("parse_date", d) == d


# --------------------------------------------------------------------------- #
# apply_transform passthrough / unknown
# --------------------------------------------------------------------------- #


def test_apply_transform_none_value_passthrough():
    # to_int would normally choke on None, but None passes through untouched
    assert apply_transform("to_int", None) is None


def test_apply_transform_empty_string_passthrough():
    assert apply_transform("to_int", "") == ""


def test_apply_transform_whitespace_only_passthrough():
    # value.strip() == "" -> treated as empty, passes through unchanged
    assert apply_transform("to_int", "   ") == "   "


def test_apply_transform_no_spec_returns_value():
    assert apply_transform(None, "untouched") == "untouched"
    assert apply_transform("", 123) == 123


def test_apply_transform_unknown_raises_transform_error():
    with pytest.raises(TransformError):
        apply_transform("does_not_exist", "value")


# --------------------------------------------------------------------------- #
# parse_transform_spec
# --------------------------------------------------------------------------- #


def test_parse_spec_name_with_arg():
    assert parse_transform_spec("parse_date:%d.%m.%Y") == ("parse_date", "%d.%m.%Y")


def test_parse_spec_bare_name():
    assert parse_transform_spec("strip") == ("strip", None)


def test_parse_spec_none():
    assert parse_transform_spec(None) == (None, None)


def test_parse_spec_empty_string():
    assert parse_transform_spec("") == (None, None)


def test_parse_spec_arg_containing_colon():
    # partition splits on the FIRST colon only
    assert parse_transform_spec("parse_date:%H:%M") == ("parse_date", "%H:%M")


def test_parse_spec_trailing_colon_empty_arg():
    assert parse_transform_spec("name:") == ("name", "")


def test_parse_spec_strips_name_whitespace():
    assert parse_transform_spec("  strip  ") == ("strip", None)


# --------------------------------------------------------------------------- #
# register_transform / available_transforms
# --------------------------------------------------------------------------- #


def test_available_transforms_includes_builtins_sorted():
    names = available_transforms()
    assert names == sorted(names)
    for builtin in ("strip", "strip_lower", "to_int", "to_float", "to_bool", "parse_date"):
        assert builtin in names


def test_register_custom_transform_usable():
    register_transform("shout", lambda v, arg: str(v).upper() + "!")
    assert "shout" in available_transforms()
    assert apply_transform("shout", "hi") == "HI!"


def test_register_custom_transform_receives_arg():
    register_transform("suffix", lambda v, arg: f"{v}{arg}")
    assert apply_transform("suffix:_X", "base") == "base_X"


def test_register_transform_decorator_form():
    @register_transform("upper")
    def _upper(value, arg):
        return str(value).upper()

    # The decorator returns the original function unchanged...
    assert _upper("hi", None) == "HI"
    # ...and the transform is registered and usable by name.
    assert "upper" in available_transforms()
    assert apply_transform("upper", "hi") == "HI"


def test_register_transform_decorator_respects_overwrite():
    register_transform("deco_dup", lambda v, arg: 1)
    with pytest.raises(ValueError):

        @register_transform("deco_dup")
        def _again(value, arg):
            return 2

    @register_transform("deco_dup", overwrite=True)
    def _replacement(value, arg):
        return 2

    assert apply_transform("deco_dup", "x") == 2


def test_register_duplicate_without_overwrite_raises():
    register_transform("dup", lambda v, arg: 1)
    with pytest.raises(ValueError):
        register_transform("dup", lambda v, arg: 2)
    # original still in place
    assert apply_transform("dup", "x") == 1


def test_register_duplicate_with_overwrite_replaces():
    register_transform("dup2", lambda v, arg: 1)
    register_transform("dup2", lambda v, arg: 2, overwrite=True)
    assert apply_transform("dup2", "x") == 2


def test_register_cannot_overwrite_builtin_without_flag():
    with pytest.raises(ValueError):
        register_transform("strip", lambda v, arg: "clobbered")
