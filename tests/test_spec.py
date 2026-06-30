"""Tests for fidelis/spec.py.

Covers: normalize_field_name, compute_signature, the Spec model and its
serialization (to_yaml_dict / dump_yaml / save / load round-trip),
find_spec_by_signature, find_drift_candidate, iter_specs, as well as
the derived properties source_fields and has_needs_review.
"""

from __future__ import annotations

import yaml

import fidelis as fp
from fidelis.spec import (
    Mapping,
    ParsingSpec,
    Spec,
    compute_signature,
    find_drift_candidate,
    find_spec_by_signature,
    iter_specs,
    normalize_field_name,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _user_spec(*, signature=None, sources=("E-mail", "Name", "Date")):
    """Build a typical Spec for the User model with three mappings."""

    targets = ("email", "full_name", "signup_date")
    mappings = [
        Mapping(target=t, source=s, transform="strip", confidence=0.9)
        for t, s in zip(targets, sources)
    ]
    if signature is None:
        signature = compute_signature(sources)
    return Spec(
        signature=signature,
        mappings=mappings,
    )


# --------------------------------------------------------------------------- #
# normalize_field_name
# --------------------------------------------------------------------------- #

def test_normalize_trims_and_lowercases():
    assert normalize_field_name("  Full Name  ") == "full name"


def test_normalize_collapses_spaces_and_underscores():
    assert normalize_field_name("full_name") == "full name"
    assert normalize_field_name("FULL   NAME") == "full name"
    assert normalize_field_name("full___name") == "full name"
    assert normalize_field_name("Full _ Name") == "full name"


def test_normalize_space_underscore_equivalent():
    assert normalize_field_name("Signup Date") == normalize_field_name("signup_date")


def test_normalize_accepts_non_str():
    # Internally str(name) is called; a number must not break normalization.
    assert normalize_field_name(123) == "123"


def test_normalize_does_not_strip_hyphen():
    # A hyphen is not part of [\s_], so it is preserved.
    assert normalize_field_name("E-mail") == "e-mail"


def test_normalize_empty_and_whitespace():
    assert normalize_field_name("") == ""
    assert normalize_field_name("   ") == ""
    assert normalize_field_name("__") == ""


# --------------------------------------------------------------------------- #
# compute_signature
# --------------------------------------------------------------------------- #

def test_signature_is_short_hex():
    sig = compute_signature(["E-mail", "Name", "Date"])
    assert isinstance(sig, str)
    assert len(sig) == 6
    int(sig, 16)  # valid hex


def test_signature_order_insensitive():
    a = compute_signature(["E-mail", "Name", "Date"])
    b = compute_signature(["Date", "E-mail", "Name"])
    assert a == b


def test_signature_case_insensitive():
    a = compute_signature(["Full Name", "Email"])
    b = compute_signature(["FULL NAME", "EMAIL"])
    assert a == b


def test_signature_space_underscore_collapsed():
    a = compute_signature(["Full Name", "Signup Date"])
    b = compute_signature(["full_name", "signup_date"])
    assert a == b


def test_signature_duplicates_collapse():
    # The set of fields is a set; duplicates have no effect.
    a = compute_signature(["Name", "Name", "Email"])
    b = compute_signature(["name", "email"])
    assert a == b


def test_signature_csv_headers_and_json_keys_match():
    """CSV headers and JSON keys with the same logical fields -> one signature."""

    csv_headers = ["E-mail", "Full Name", "Signup Date"]
    json_keys = ["e-mail", "full_name", "signup date"]
    assert compute_signature(csv_headers) == compute_signature(json_keys)


def test_signature_different_fields_differ():
    a = compute_signature(["Email", "Name"])
    b = compute_signature(["Email", "Name", "Phone"])
    assert a != b


def test_signature_matches_module_function():
    # compute_signature exported from the package is the same function.
    assert fp.compute_signature is compute_signature


# --------------------------------------------------------------------------- #
# Spec defaults and derived properties
# --------------------------------------------------------------------------- #

def test_spec_defaults():
    spec = Spec(signature="abc123")
    assert spec.version == 1
    assert spec.generated_by == "human"
    assert spec.generated_at is None
    assert spec.parsing is None
    assert spec.mappings == []
    assert spec.on_unknown_column is None


def test_source_fields_are_normalized():
    spec = _user_spec(sources=("E-mail", "Full Name", "Signup_Date"))
    assert spec.source_fields == {"e-mail", "full name", "signup date"}


def test_source_fields_empty_when_no_mappings():
    spec = Spec(signature="abc123")
    assert spec.source_fields == set()


def test_has_needs_review_false_by_default():
    spec = _user_spec()
    assert spec.has_needs_review is False


def test_has_needs_review_true_when_any_flagged():
    spec = _user_spec()
    spec.mappings[2].status = "needs_review"
    assert spec.has_needs_review is True


# --------------------------------------------------------------------------- #
# to_yaml_dict / dump_yaml — ordering, rounding, absence of None
# --------------------------------------------------------------------------- #

def test_to_yaml_dict_omits_none_fields():
    spec = _user_spec()  # generated_at, parsing, on_unknown_column = None
    data = spec.to_yaml_dict()
    assert "generated_at" not in data
    assert "parsing" not in data
    assert "on_unknown_column" not in data


def test_to_yaml_dict_includes_present_optional_fields():
    spec = _user_spec()
    spec.generated_at = "2026-06-28T00:00:00Z"
    spec.parsing = ParsingSpec(delimiter=";")
    spec.on_unknown_column = "error"
    data = spec.to_yaml_dict()
    assert data["generated_at"] == "2026-06-28T00:00:00Z"
    assert data["parsing"] == {"delimiter": ";"}  # encoding/quote_char (None) dropped
    assert data["on_unknown_column"] == "error"


def test_to_yaml_dict_empty_parsing_omitted():
    # A ParsingSpec with all None must not end up in the dict.
    spec = _user_spec()
    spec.parsing = ParsingSpec()
    data = spec.to_yaml_dict()
    assert "parsing" not in data


def test_to_yaml_dict_mapping_transform_omitted_when_none():
    spec = Spec(
        signature="abc123",
        mappings=[Mapping(target="email", source="E-mail")],
    )
    entry = spec.to_yaml_dict()["mappings"][0]
    assert "transform" not in entry
    assert entry["target"] == "email"
    assert entry["source"] == "E-mail"
    assert entry["confidence"] == 1.0
    assert entry["status"] == "ok"


def test_to_yaml_dict_rounds_confidence():
    spec = Spec(
        signature="abc123",
        mappings=[Mapping(target="email", source="E-mail", confidence=0.123456789)],
    )
    entry = spec.to_yaml_dict()["mappings"][0]
    assert entry["confidence"] == 0.1235


def test_dump_yaml_is_parseable_and_unicode():
    spec = _user_spec(sources=("Mail", "Name", "Date"))
    text = spec.dump_yaml()
    # allow_unicode=True → unicode is not escaped.
    assert "Mail" in text
    loaded = yaml.safe_load(text)
    assert loaded["signature"]


def test_dump_yaml_preserves_key_order():
    spec = _user_spec()
    text = spec.dump_yaml()
    keys = [k for k in ("version", "generated_by", "signature", "mappings") ]
    positions = [text.index(k + ":") for k in keys]
    assert positions == sorted(positions)


# --------------------------------------------------------------------------- #
# YAML round-trip
# --------------------------------------------------------------------------- #

def test_from_yaml_round_trip_preserves_core_fields():
    spec = _user_spec()
    spec.mappings[0].transform = "strip_lower"
    spec.mappings[0].confidence = 0.98
    spec.mappings[2].status = "needs_review"
    spec.mappings[2].confidence = 0.62

    restored = Spec.from_yaml(spec.dump_yaml())

    assert restored.signature == spec.signature
    assert len(restored.mappings) == 3
    assert restored.mappings[0].transform == "strip_lower"
    assert restored.mappings[0].confidence == 0.98
    assert restored.mappings[2].status == "needs_review"
    assert restored.mappings[2].confidence == 0.62
    assert restored.has_needs_review is True


def test_round_trip_full_equality():
    spec = _user_spec()
    spec.generated_at = "2026-06-28T00:00:00Z"
    spec.parsing = ParsingSpec(delimiter=";", encoding="utf-8")
    spec.on_unknown_column = "regenerate"
    restored = Spec.from_yaml(spec.dump_yaml())
    assert restored == spec


def test_save_and_load_round_trip(spec_dir):
    spec = _user_spec()
    path = spec.save(spec_dir)
    assert path.exists()
    assert path.name == f"spec_{spec.signature}.yaml"
    assert path.parent == spec_dir

    loaded = Spec.load(path)
    assert loaded == spec


def test_save_creates_missing_directory(tmp_path):
    target = tmp_path / "nested" / "specs"
    spec = _user_spec()
    path = spec.save(target)
    assert path.exists()
    assert target.is_dir()


def test_saved_yaml_has_no_none_keys(spec_dir):
    spec = _user_spec()  # all optionals = None
    path = spec.save(spec_dir)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "generated_at" not in raw
    assert "parsing" not in raw
    assert "on_unknown_column" not in raw
    for entry in raw["mappings"]:
        assert "transform" in entry  # _user_spec sets transform="strip"


# --------------------------------------------------------------------------- #
# find_spec_by_signature
# --------------------------------------------------------------------------- #

def test_find_spec_by_signature_found(spec_dir):
    spec = _user_spec()
    spec.save(spec_dir)
    found = find_spec_by_signature(spec_dir, spec.signature)
    assert found is not None
    assert found.signature == spec.signature


def test_find_spec_by_signature_not_found(spec_dir):
    _user_spec().save(spec_dir)
    assert find_spec_by_signature(spec_dir, "ffffff") is None


def test_find_spec_by_signature_missing_dir(tmp_path):
    assert find_spec_by_signature(tmp_path / "nope", "abc123") is None


def test_find_spec_by_signature_skips_invalid_yaml(spec_dir):
    spec = _user_spec()
    spec.save(spec_dir)
    (spec_dir / "broken.yaml").write_text("this: is: not: valid: spec", encoding="utf-8")
    found = find_spec_by_signature(spec_dir, spec.signature)
    assert found is not None
    assert found.signature == spec.signature


def test_find_spec_matches_signature_across_csv_and_json(spec_dir):
    """A single saved spec is found by a signature computed from JSON keys."""

    csv_sources = ("E-mail", "Full Name", "Signup Date")
    spec = _user_spec(sources=csv_sources)
    spec.save(spec_dir)
    json_sig = compute_signature(["e-mail", "full_name", "signup date"])
    assert json_sig == spec.signature
    assert find_spec_by_signature(spec_dir, json_sig) is not None


# --------------------------------------------------------------------------- #
# iter_specs
# --------------------------------------------------------------------------- #

def test_iter_specs_empty_dir(spec_dir):
    assert iter_specs(spec_dir) == []


def test_iter_specs_missing_dir(tmp_path):
    assert iter_specs(tmp_path / "nope") == []


def test_iter_specs_loads_all_and_skips_invalid(spec_dir):
    # Two distinct sources → two distinct signatures → two files.
    a = _user_spec(sources=("E-mail", "Name", "Date"))
    b = _user_spec(sources=("Mail", "Person", "When"))
    a.save(spec_dir)
    b.save(spec_dir)
    (spec_dir / "junk.yml").write_text("- not a mapping dict", encoding="utf-8")
    specs = iter_specs(spec_dir)
    assert {s.signature for s in specs} == {a.signature, b.signature}


def test_iter_specs_picks_up_yml_and_yaml_extensions(spec_dir):
    a = _user_spec(sources=("E-mail", "Name", "Date"))
    a.save(spec_dir)  # .yaml
    # write a second one (different signature) manually as .yml
    b = _user_spec(sources=("Mail", "Person", "When"))
    (spec_dir / "b.yml").write_text(b.dump_yaml(), encoding="utf-8")
    sigs = {s.signature for s in iter_specs(spec_dir)}
    assert sigs == {a.signature, b.signature}


# --------------------------------------------------------------------------- #
# find_drift_candidate
# --------------------------------------------------------------------------- #

def test_drift_candidate_returns_near_match(spec_dir):
    """Drift of a familiar source (a field was added) → returns the spec."""

    saved = _user_spec(sources=("E-mail", "Name", "Date"))
    saved.save(spec_dir)
    # 3 in common out of 4 in the union → Jaccard 0.75 > 0.5.
    drifted = ["E-mail", "Name", "Date", "Extra"]
    found = find_drift_candidate(spec_dir, drifted)
    assert found is not None
    assert found.signature == saved.signature


def test_drift_candidate_none_for_unrelated_fields(spec_dir):
    _user_spec(sources=("E-mail", "Name", "Date")).save(spec_dir)
    found = find_drift_candidate(spec_dir, ["foo", "bar", "baz"])
    assert found is None


def test_drift_candidate_normalizes_field_names(spec_dir):
    """Match by normalized names (case/underscores)."""

    _user_spec(sources=("E-mail", "Full Name", "Signup Date")).save(spec_dir)
    drifted = ["E-MAIL", "full_name", "signup date", "newcol"]
    found = find_drift_candidate(spec_dir, drifted)
    assert found is not None


def test_drift_candidate_empty_field_names(spec_dir):
    _user_spec().save(spec_dir)
    assert find_drift_candidate(spec_dir, []) is None


def test_drift_candidate_empty_spec_dir(spec_dir):
    assert find_drift_candidate(spec_dir, ["E-mail", "Name", "Date"]) is None


def test_drift_candidate_respects_threshold(spec_dir):
    """With a high threshold, a weak overlap is not considered a candidate."""

    _user_spec(sources=("E-mail", "Name", "Date")).save(spec_dir)
    # 1 in common (e-mail) out of a union of 4 → 0.25.
    fields = ["E-mail", "x", "y"]
    assert find_drift_candidate(spec_dir, fields, min_similarity=0.5) is None
    assert find_drift_candidate(spec_dir, fields, min_similarity=0.2) is not None


def test_drift_candidate_threshold_boundary_inclusive(spec_dir):
    """score == min_similarity counts as a match (>= threshold)."""

    # source fields {a, b}; actual {a, b, c, d} → 2/4 = 0.5.
    spec = Spec(
        signature=compute_signature(["a", "b"]),
        mappings=[
            Mapping(target="email", source="a"),
            Mapping(target="full_name", source="b"),
        ],
    )
    spec.save(spec_dir)
    found = find_drift_candidate(spec_dir, ["a", "b", "c", "d"], min_similarity=0.5)
    assert found is not None
    assert found.signature == spec.signature


def test_drift_candidate_picks_best_of_several(spec_dir):
    users = _user_spec(sources=("E-mail", "Name", "Date"))
    users.save(spec_dir)
    Spec(
        signature=compute_signature(["SKU", "Price", "InStock"]),
        mappings=[
            Mapping(target="sku", source="SKU"),
            Mapping(target="price", source="Price"),
            Mapping(target="in_stock", source="InStock"),
        ],
    ).save(spec_dir)

    found = find_drift_candidate(spec_dir, ["E-mail", "Name", "Date", "Phone"])
    assert found is not None
    assert found.signature == users.signature


def test_drift_candidate_ignores_specs_without_mappings(spec_dir):
    # A spec without mappings (empty source_fields) must not be selected.
    Spec(signature="abc123").save(spec_dir)
    assert find_drift_candidate(spec_dir, ["E-mail", "Name"]) is None
