"""Integration tests for fidelis.parser.Parser.

Cover end-to-end behaviors of parser.py not present in test_acceptance.py:
- generate_spec() returns and saves a draft spec WITHOUT parsing rows;
- validate_spec() catches unknown/duplicate target, unknown transform,
  out-of-range confidence, and an uncovered required model field;
- SpecNotFoundError when there is no spec and llm=None;
- kind inference from file extension and from source type (list vs inline str);
- end-to-end Excel (xlsx) parsing via FakeProvider;
- faithful use of spec.parsing.delimiter when re-parsing a CSV path.

Each test is self-contained and independent of the others.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

import fidelis as fp
from conftest import USER_MAPPINGS, Product, User


# --------------------------------------------------------------------------- #
# Helpers for building specs for validate_spec
# --------------------------------------------------------------------------- #


def _full_user_mappings() -> list[fp.Mapping]:
    """A complete, valid set of mappings covering every required User field."""

    return [
        fp.Mapping(
            target="email", source="E-mail", transform="strip_lower", confidence=0.98
        ),
        fp.Mapping(
            target="full_name", source="Name", transform="strip", confidence=0.91
        ),
        fp.Mapping(
            target="signup_date",
            source="Date",
            transform="parse_date:%d.%m.%Y",
            confidence=0.62,
        ),
    ]


def _spec(mappings: list[fp.Mapping]) -> fp.Spec:
    return fp.Spec(
        signature=fp.compute_signature(["E-mail", "Name", "Date"]),
        mappings=mappings,
    )


def _user_provider() -> fp.FakeProvider:
    return fp.FakeProvider(mappings=USER_MAPPINGS)


# --------------------------------------------------------------------------- #
# generate_spec()
# --------------------------------------------------------------------------- #


def test_generate_spec_returns_and_saves_draft_without_parsing(spec_dir, user_records):
    """generate_spec() returns a Spec, writes YAML, and does NOT parse/validate rows."""

    provider = _user_provider()
    parser = fp.Parser(User, spec_store=spec_dir, llm=provider)

    # Mix in a deliberately invalid row: if generate_spec parsed rows, it would
    # either fail or land in errors. But generate_spec never touches the rows.
    bad_records = user_records + [
        {"E-mail": "not-an-email", "Name": "X", "Date": "not-a-date"}
    ]

    spec = parser.generate_spec(bad_records)

    # A Spec is returned (not a ParseResult) — no valid_rows/errors.
    assert isinstance(spec, fp.Spec)
    assert not isinstance(spec, fp.ParseResult)

    # The LLM was called exactly once (one-off inference); rows never go to the LLM.
    assert provider.call_count == 1

    # The signature is derived from the source field inventory.
    expected_sig = fp.compute_signature(["E-mail", "Name", "Date"])
    assert spec.signature == expected_sig

    # Mappings cover the target model.
    assert {m.target for m in spec.mappings} == {"email", "full_name", "signup_date"}

    # The spec is saved to disk (keyed by signature) and reads back equivalently.
    path = spec_dir / f"spec_{spec.signature}.yaml"
    assert path.exists()
    reloaded = fp.Spec.load(path)
    assert reloaded.signature == spec.signature
    assert {m.target for m in reloaded.mappings} == {
        "email",
        "full_name",
        "signup_date",
    }


def test_generate_spec_requires_llm(spec_dir, user_records):
    """Without a configured LLM, generate_spec raises SpecNotFoundError."""

    parser = fp.Parser(User, spec_store=spec_dir, llm=None)
    with pytest.raises(fp.SpecNotFoundError):
        parser.generate_spec(user_records)


# --------------------------------------------------------------------------- #
# validate_spec()
# --------------------------------------------------------------------------- #


def test_validate_spec_accepts_fully_valid_spec(spec_dir):
    """A fully correct spec produces no problems at all."""

    parser = fp.Parser(User, spec_store=spec_dir, llm=None)
    problems = parser.validate_spec(_spec(_full_user_mappings()))
    assert problems == []


def test_validate_spec_flags_unknown_target(spec_dir):
    """A mapping to a nonexistent model field is flagged as a problem."""

    parser = fp.Parser(User, spec_store=spec_dir, llm=None)
    mappings = _full_user_mappings() + [
        fp.Mapping(target="bogus_field", source="Extra", confidence=0.9)
    ]
    problems = parser.validate_spec(_spec(mappings))

    assert any("bogus_field" in p for p in problems)
    # This is the only problem: required fields are still covered.
    assert not any("not covered" in p for p in problems)


def test_validate_spec_flags_duplicate_target(spec_dir):
    """A single target mapped twice is flagged as a duplication problem."""

    parser = fp.Parser(User, spec_store=spec_dir, llm=None)
    mappings = _full_user_mappings() + [
        fp.Mapping(target="email", source="Email2", confidence=0.9)
    ]
    problems = parser.validate_spec(_spec(mappings))

    assert any("email" in p and "more than once" in p for p in problems)


def test_validate_spec_flags_unknown_transform(spec_dir):
    """An unknown transform in a mapping is flagged as a problem."""

    parser = fp.Parser(User, spec_store=spec_dir, llm=None)
    mappings = _full_user_mappings()
    # Replace the valid strip_lower with a nonexistent transform.
    mappings[0] = fp.Mapping(
        target="email", source="E-mail", transform="frobnicate", confidence=0.98
    )
    problems = parser.validate_spec(_spec(mappings))

    assert any("frobnicate" in p and "transform" in p for p in problems)


def test_validate_spec_flags_out_of_range_confidence(spec_dir):
    """A confidence outside [0,1] is flagged as a problem."""

    parser = fp.Parser(User, spec_store=spec_dir, llm=None)
    mappings = _full_user_mappings()
    mappings[0] = fp.Mapping(
        target="email", source="E-mail", transform="strip_lower", confidence=1.5
    )
    mappings[1] = fp.Mapping(
        target="full_name", source="Name", transform="strip", confidence=-0.2
    )
    problems = parser.validate_spec(_spec(mappings))

    conf_problems = [p for p in problems if "confidence" in p]
    assert any("email" in p for p in conf_problems)
    assert any("full_name" in p for p in conf_problems)


def test_validate_spec_flags_uncovered_required_field(spec_dir):
    """A required model field with no mapping is flagged as not covered."""

    parser = fp.Parser(User, spec_store=spec_dir, llm=None)
    # Cover only email and full_name; signup_date (required) is omitted.
    mappings = _full_user_mappings()[:2]
    problems = parser.validate_spec(_spec(mappings))

    assert any("signup_date" in p and "not covered" in p for p in problems)


def test_validate_spec_loads_from_path(spec_dir):
    """validate_spec accepts a YAML path and loads the spec itself."""

    parser = fp.Parser(User, spec_store=spec_dir, llm=None)
    spec = _spec(_full_user_mappings())
    path = spec.save(spec_dir)

    # Both str and PathLike forms are valid.
    assert parser.validate_spec(str(path)) == []
    assert parser.validate_spec(path) == []


# --------------------------------------------------------------------------- #
# SpecNotFoundError
# --------------------------------------------------------------------------- #


def test_parse_raises_spec_not_found_without_spec_and_llm(spec_dir, user_records):
    """No matching spec and llm=None → SpecNotFoundError on parse()."""

    parser = fp.Parser(User, spec_store=spec_dir, llm=None)
    assert fp.find_spec_by_signature(spec_dir, fp.compute_signature(["E-mail", "Name", "Date"])) is None
    with pytest.raises(fp.SpecNotFoundError):
        parser.parse(user_records)


def test_parse_uses_existing_spec_without_llm(spec_dir, user_records):
    """If a spec is already on disk, parse() works even with llm=None."""

    # Drop in a ready, valid spec whose signature matches user_records.
    _spec(_full_user_mappings()).save(spec_dir)

    parser = fp.Parser(User, spec_store=spec_dir, llm=None)
    result = parser.parse(user_records)

    assert result.spec_generated is False
    assert result.valid_rows[0] == User(
        email="a@b.com", full_name="Alice", signup_date=date(2026, 2, 1)
    )


# --------------------------------------------------------------------------- #
# kind inference
# --------------------------------------------------------------------------- #


def test_infer_kind_by_extension():
    """kind is determined by the file extension."""

    assert fp.Parser._infer_kind("feed.csv") == "csv"
    assert fp.Parser._infer_kind("feed.tsv") == "csv"
    assert fp.Parser._infer_kind("feed.txt") == "csv"
    assert fp.Parser._infer_kind("feed.json") == "json"
    assert fp.Parser._infer_kind("feed.xlsx") == "excel"
    assert fp.Parser._infer_kind("feed.xls") == "excel"
    assert fp.Parser._infer_kind("feed.xlsm") == "excel"
    # Extension case does not matter.
    assert fp.Parser._infer_kind("FEED.XLSX") == "excel"


def test_infer_kind_list_vs_inline_string():
    """list/dict → records; a string without an extension → inline CSV; bytes → csv."""

    assert fp.Parser._infer_kind([{"a": 1}]) == "records"
    assert fp.Parser._infer_kind({"a": 1}) == "records"
    # A multi-line string with a header is inline CSV text, not a path.
    assert fp.Parser._infer_kind("E-mail;Name;Date\na@b.com;A;01.02.2026") == "csv"
    assert fp.Parser._infer_kind(b"E-mail;Name;Date\n") == "csv"


def test_json_extension_inferred_end_to_end(spec_dir, tmp_path):
    """parse() on a .json path picks the JSON adapter and parses the data correctly."""

    records = [
        {"E-mail": " A@B.COM ", "Name": " Alice ", "Date": "01.02.2026"},
        {"E-mail": "bob@x.com", "Name": "Bob", "Date": "15.03.2026"},
    ]
    path = tmp_path / "users.json"
    path.write_text(json.dumps(records), encoding="utf-8")

    parser = fp.Parser(User, spec_store=spec_dir, llm=_user_provider())
    result = parser.parse(str(path))  # kind not given — inferred from .json

    # JSON is structured — Stage 1 (parsing) is not needed.
    assert result.spec_used.parsing is None
    assert len(result.valid_rows) == 2
    assert result.valid_rows[0] == User(
        email="a@b.com", full_name="Alice", signup_date=date(2026, 2, 1)
    )


# --------------------------------------------------------------------------- #
# End-to-end Excel
# --------------------------------------------------------------------------- #


def test_excel_parse_end_to_end(spec_dir, tmp_path):
    """Generating an xlsx + FakeProvider → correct end-to-end Excel parsing."""

    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["E-mail", "Name", "Date"])
    ws.append([" A@B.COM ", " Alice ", "01.02.2026"])
    ws.append(["bob@x.com", "Bob", "15.03.2026"])
    xlsx_path = tmp_path / "users.xlsx"
    wb.save(xlsx_path)

    provider = _user_provider()
    parser = fp.Parser(User, spec_store=spec_dir, llm=provider)
    result = parser.parse(str(xlsx_path))  # kind inferred as excel

    assert provider.call_count == 1  # spec generated once
    assert result.spec_generated is True
    # Excel is structured — no parsing section.
    assert result.spec_used.parsing is None
    assert len(result.valid_rows) == 2
    assert result.errors == []
    assert result.valid_rows[0] == User(
        email="a@b.com", full_name="Alice", signup_date=date(2026, 2, 1)
    )
    assert result.valid_rows[1] == User(
        email="bob@x.com", full_name="Bob", signup_date=date(2026, 3, 15)
    )


# --------------------------------------------------------------------------- #
# spec.parsing.delimiter on re-parsing a CSV path
# --------------------------------------------------------------------------- #


def test_spec_delimiter_honored_on_reparse_of_csv_path(tmp_path):
    """The delimiter from spec.parsing is applied when re-parsing a CSV file.

    The file is written with the '|' delimiter. One spec carries delimiter='|'
    (correct), another delimiter=';' (wrong). Different parse results for the same
    file prove that re-parsing faithfully uses spec.parsing rather than running
    auto-detection again.
    """

    csv_path = tmp_path / "pipe.csv"
    csv_path.write_text(
        "E-mail|Name|Date\n A@B.COM | Alice |01.02.2026\nbob@x.com|Bob|15.03.2026\n",
        encoding="utf-8",
    )

    signature = fp.compute_signature(["E-mail", "Name", "Date"])

    def _make_spec_dir(delimiter: str):
        d = tmp_path / f"specs_{delimiter!r}"
        d.mkdir()
        spec = fp.Spec(
            signature=signature,
            parsing=fp.ParsingSpec(delimiter=delimiter),
            mappings=_full_user_mappings(),
        )
        spec.save(d)
        return d

    # Correct delimiter '|' → re-parsing splits the row into 3 fields correctly.
    good_dir = _make_spec_dir("|")
    good_parser = fp.Parser(User, spec_store=good_dir, llm=None)
    good = good_parser.parse(str(csv_path))
    assert good.spec_generated is False
    assert good.spec_used.parsing.delimiter == "|"
    assert len(good.valid_rows) == 2
    assert good.valid_rows[0] == User(
        email="a@b.com", full_name="Alice", signup_date=date(2026, 2, 1)
    )

    # Wrong delimiter ';' → the whole row becomes a single field, source fields
    # don't resolve, required model fields are empty → rows go to errors, none
    # valid. This is the visible effect of spec.parsing.delimiter.
    bad_dir = _make_spec_dir(";")
    bad_parser = fp.Parser(User, spec_store=bad_dir, llm=None)
    bad = bad_parser.parse(str(csv_path))
    assert bad.spec_used.parsing.delimiter == ";"
    assert bad.valid_rows == []
    # Both rows dropped out (by index); none silently lost.
    assert {e.row_index for e in bad.errors} == {0, 1}


def test_product_spec_delimiter_roundtrip_via_generation(spec_dir, tmp_path):
    """A generated spec stores the auto-detected delimiter and re-parses faithfully."""

    csv_path = tmp_path / "products.csv"
    csv_path.write_text(
        "SKU;Price;Stock\nA-1;9.99;yes\nB-2;19.50;no\n",
        encoding="utf-8",
    )

    mappings = [
        {"source": "SKU", "target": "sku", "transform": "strip", "confidence": 0.95},
        {"source": "Price", "target": "price", "transform": "to_float", "confidence": 0.9},
        {"source": "Stock", "target": "in_stock", "transform": "to_bool", "confidence": 0.85},
    ]
    provider = fp.FakeProvider(mappings=mappings)
    parser = fp.Parser(Product, spec_store=spec_dir, llm=provider)

    first = parser.parse(str(csv_path))
    assert first.spec_generated is True
    # Auto-detected ';' landed in spec.parsing.
    assert first.spec_used.parsing is not None
    assert first.spec_used.parsing.delimiter == ";"

    # A repeat run with no new LLM calls uses the stored delimiter.
    second = parser.parse(str(csv_path))
    assert second.spec_generated is False
    assert provider.call_count == 1
    assert len(second.valid_rows) == 2
    assert second.valid_rows[0] == Product(sku="A-1", price=9.99, in_stock=True)
    assert second.valid_rows[1] == Product(sku="B-2", price=19.50, in_stock=False)
