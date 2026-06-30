"""Tests for schema drift detection: detect_drift, DriftReport, DriftError and
integration through Parser for all three on_unknown_column policies."""

from __future__ import annotations

from datetime import date

import pytest

import fidelis as fp
from conftest import USER_MAPPINGS, User


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_TARGET_PATH = f"{User.__module__}.{User.__qualname__}"


def _make_spec(source_to_target, *, on_unknown=None, transforms=None):
    """Build a Spec by hand from (source -> target) pairs.

    signature is computed from the original source names so that the spec is
    actually found by signature lookup when the field set does not change.
    """

    transforms = transforms or {}
    mappings = [
        fp.Mapping(
            source=src,
            target=tgt,
            transform=transforms.get(src),
            confidence=0.95,
        )
        for src, tgt in source_to_target.items()
    ]
    return fp.Spec(
        signature=fp.compute_signature(list(source_to_target.keys())),
        mappings=mappings,
        on_unknown_column=on_unknown,
    )


def _user_spec(**kwargs):
    return _make_spec(
        {"E-mail": "email", "Name": "full_name", "Date": "signup_date"}, **kwargs
    )


# --------------------------------------------------------------------------- #
# detect_drift: new fields
# --------------------------------------------------------------------------- #


def test_detect_drift_reports_new_field_when_source_gains_column():
    spec = _user_spec()
    report = fp.detect_drift(spec, ["E-mail", "Name", "Date", "Phone"])

    assert report.has_drift is True
    assert report.new_fields == ["Phone"]
    assert report.missing_fields == []


def test_detect_drift_new_field_surfaces_original_name_not_normalized():
    """Comparison is normalized, but the report keeps the original name as it arrived."""

    spec = _make_spec({"E-mail": "email"})
    report = fp.detect_drift(spec, ["E-mail", "Phone Number"])

    assert report.new_fields == ["Phone Number"]
    assert report.missing_fields == []
    assert report.has_drift is True


def test_detect_drift_multiple_new_fields_sorted():
    spec = _make_spec({"E-mail": "email"})
    report = fp.detect_drift(spec, ["E-mail", "Zeta", "Alpha"])

    assert report.new_fields == ["Alpha", "Zeta"]  # sorted
    assert report.missing_fields == []


# --------------------------------------------------------------------------- #
# detect_drift: missing fields
# --------------------------------------------------------------------------- #


def test_detect_drift_reports_missing_field_when_source_loses_column():
    spec = _user_spec()
    report = fp.detect_drift(spec, ["E-mail", "Name"])

    assert report.has_drift is True
    assert report.missing_fields == ["Date"]
    assert report.new_fields == []


def test_detect_drift_missing_field_surfaces_original_source_name():
    """For missing fields, the report uses the original source string from the mapping."""

    spec = _make_spec({"E-mail": "email", "Sign Up Date": "signup_date"})
    # The source arrived in a different case and without the 'Sign Up Date' field.
    report = fp.detect_drift(spec, ["e-mail"])

    assert report.missing_fields == ["Sign Up Date"]
    assert report.new_fields == []


def test_detect_drift_reports_both_new_and_missing():
    spec = _user_spec()
    report = fp.detect_drift(spec, ["E-mail", "Name", "Phone"])  # lost Date, gained Phone

    assert report.has_drift is True
    assert report.new_fields == ["Phone"]
    assert report.missing_fields == ["Date"]


# --------------------------------------------------------------------------- #
# detect_drift: no drift modulo case/whitespace
# --------------------------------------------------------------------------- #


def test_no_drift_when_field_sets_match_modulo_case_and_spacing():
    spec = _user_spec()
    report = fp.detect_drift(spec, ["e-mail", "  NAME ", "date"])

    assert report.has_drift is False
    assert report.new_fields == []
    assert report.missing_fields == []
    assert bool(report) is False


def test_no_drift_when_underscores_collapse_to_match():
    """Normalization collapses spaces/underscores — 'sign_up date' == 'Sign Up Date'."""

    spec = _make_spec({"Sign Up Date": "signup_date"})
    report = fp.detect_drift(spec, ["sign_up   date"])

    assert report.has_drift is False
    assert report.missing_fields == []
    assert report.new_fields == []


# --------------------------------------------------------------------------- #
# DriftReport: __bool__ / describe / action
# --------------------------------------------------------------------------- #


def test_driftreport_bool_true_when_drift():
    report = fp.detect_drift(_user_spec(), ["E-mail", "Name", "Date", "Extra"])
    assert bool(report) is True
    assert report  # truthy


def test_driftreport_bool_false_when_no_drift():
    report = fp.detect_drift(_user_spec(), ["E-mail", "Name", "Date"])
    assert bool(report) is False
    assert not report


def test_driftreport_describe_no_drift():
    report = fp.DriftReport()
    assert report.describe() == "No schema drift detected."


def test_driftreport_describe_lists_new_and_missing_and_action():
    report = fp.DriftReport(
        has_drift=True,
        new_fields=["Phone"],
        missing_fields=["Date"],
        action="error",
    )
    text = report.describe()
    assert "Phone" in text
    assert "Date" in text
    assert "new fields" in text
    assert "missing fields" in text
    assert "error" in text


def test_driftreport_describe_only_new():
    report = fp.DriftReport(has_drift=True, new_fields=["Phone"], action="ignore")
    text = report.describe()
    assert "new fields" in text
    assert "missing fields" not in text


def test_driftreport_default_action_is_none():
    report = fp.detect_drift(_user_spec(), ["E-mail", "Name", "Date", "Extra"])
    assert report.action == "none"


# --------------------------------------------------------------------------- #
# DriftError carries the report
# --------------------------------------------------------------------------- #


def test_drifterror_carries_report_and_message():
    report = fp.DriftReport(has_drift=True, new_fields=["Extra"], action="error")
    err = fp.DriftError(report)

    assert isinstance(err, ValueError)
    assert err.report is report
    assert str(err) == report.describe()
    assert "Extra" in str(err)


# --------------------------------------------------------------------------- #
# Integration through Parser: error policy
# --------------------------------------------------------------------------- #


def test_parser_on_unknown_error_raises_drifterror(spec_dir, user_records):
    provider = fp.FakeProvider(mappings=USER_MAPPINGS)
    parser = fp.Parser(
        User, spec_store=spec_dir, llm=provider, on_unknown_column="error"
    )
    # The first run creates the spec for {E-mail, Name, Date}.
    parser.parse(user_records)

    drifted = [{"E-mail": "a@b.com", "Name": "A", "Date": "01.02.2026", "Extra": "x"}]
    with pytest.raises(fp.DriftError) as exc:
        parser.parse(drifted)

    report = exc.value.report
    assert report.has_drift is True
    assert report.new_fields == ["Extra"]
    assert report.action == "error"


# --------------------------------------------------------------------------- #
# Integration through Parser: ignore policy
# --------------------------------------------------------------------------- #


def test_parser_on_unknown_ignore_proceeds_and_surfaces_drift(spec_dir, user_records):
    provider = fp.FakeProvider(mappings=USER_MAPPINGS)
    parser = fp.Parser(
        User, spec_store=spec_dir, llm=provider, on_unknown_column="ignore"
    )
    parser.parse(user_records)  # creates the spec
    assert provider.call_count == 1

    drifted = [
        {"E-mail": " A@B.COM ", "Name": " Alice ", "Date": "01.02.2026", "Extra": "x"}
    ]
    result = parser.parse(drifted)

    # Drift did not stop parsing — the rows went through.
    assert len(result.valid_rows) == 1
    assert result.valid_rows[0] == User(
        email="a@b.com", full_name="Alice", signup_date=date(2026, 2, 1)
    )
    # Drift is visible in the result.
    assert result.drift_report.has_drift is True
    assert result.drift_report.new_fields == ["Extra"]
    assert result.drift_report.action == "ignore"
    # ignore does not call the LLM again.
    assert provider.call_count == 1


# --------------------------------------------------------------------------- #
# Integration through Parser: regenerate policy
# --------------------------------------------------------------------------- #


def test_parser_on_unknown_regenerate_calls_provider_and_merges_new_mapping(spec_dir):
    # The base spec covers only E-mail and Name (no Date).
    base = _make_spec(
        {"E-mail": "email", "Name": "full_name"},
        transforms={"E-mail": "strip_lower", "Name": "strip"},
    )
    base.save(spec_dir)

    provider = fp.FakeProvider(mappings=USER_MAPPINGS)  # will also return Date->signup_date
    parser = fp.Parser(
        User, spec_store=spec_dir, llm=provider, on_unknown_column="regenerate"
    )

    # The source gained a Date column — a new field relative to the base spec.
    records = [{"E-mail": " A@B.COM ", "Name": " Alice ", "Date": "01.02.2026"}]
    result = parser.parse(records)

    # regenerate called the provider exactly once.
    assert provider.call_count == 1

    # The new mapping (Date -> signup_date) is merged into the spec.
    targets = {m.target for m in result.spec_used.mappings}
    assert targets == {"email", "full_name", "signup_date"}
    signup_mapping = next(
        m for m in result.spec_used.mappings if m.target == "signup_date"
    )
    assert fp.normalize_field_name(signup_mapping.source) == "date"

    # Drift is recorded in the report.
    assert result.drift_report.has_drift is True
    assert result.drift_report.new_fields == ["Date"]
    assert result.drift_report.action == "regenerate"

    # The new field was actually mapped into the target model.
    assert len(result.valid_rows) == 1
    assert result.valid_rows[0] == User(
        email="a@b.com", full_name="Alice", signup_date=date(2026, 2, 1)
    )


def test_parser_regenerate_persists_merged_spec_to_disk(spec_dir):
    base = _make_spec(
        {"E-mail": "email", "Name": "full_name"},
        transforms={"E-mail": "strip_lower", "Name": "strip"},
    )
    base.save(spec_dir)

    provider = fp.FakeProvider(mappings=USER_MAPPINGS)
    parser = fp.Parser(
        User, spec_store=spec_dir, llm=provider, on_unknown_column="regenerate"
    )
    records = [{"E-mail": "a@b.com", "Name": "Alice", "Date": "01.02.2026"}]
    parser.parse(records)

    # The merged spec is persisted under the new (drifted) field signature.
    reloaded = fp.find_spec_by_signature(
        spec_dir, fp.compute_signature(["E-mail", "Name", "Date"])
    )
    assert reloaded is not None
    assert {m.target for m in reloaded.mappings} == {
        "email",
        "full_name",
        "signup_date",
    }
