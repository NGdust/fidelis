"""Tests for the quarantine round-trip (fidelis/quarantine.py + wiring).

Covers:
- source_row is attached to RowErrors from transforms, enrichment, and
  validation;
- quarantine_rows / write_quarantine / read_quarantine (CSV and JSON);
- the full loop: parse -> write -> (fix) -> read -> parse again, with the fixed
  rows landing as valid and matching the same spec (no drift);
- the CLI --quarantine flag.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

import fidelis as fp
from fidelis import cli
from fidelis.quarantine import (
    ERROR_REASON,
    ROW_INDEX,
    quarantine_rows,
    read_quarantine,
    write_quarantine,
)

from conftest import User


def _user_spec(spec_dir):
    fp.Spec(
        signature=fp.compute_signature(["E-mail", "Name", "Date"]),
        mappings=[
            fp.Mapping(target="email", source="E-mail", transform="strip_lower"),
            fp.Mapping(target="full_name", source="Name", transform="strip"),
            fp.Mapping(
                target="signup_date", source="Date", transform="parse_date:%d.%m.%Y"
            ),
        ],
    ).save(spec_dir)


RECORDS = [
    {"E-mail": "a@b.com", "Name": "Alice", "Date": "01.02.2026"},
    {"E-mail": "bob@x.com", "Name": "Bob", "Date": "not-a-date"},  # transform fails
]


# --------------------------------------------------------------------------- #
# source_row attached to errors
# --------------------------------------------------------------------------- #


def test_transform_error_carries_source_row(spec_dir):
    _user_spec(spec_dir)
    result = fp.Parser(User, spec_store=spec_dir, llm=None).parse(RECORDS)
    assert len(result.errors) == 1
    assert result.errors[0].source_row == RECORDS[1]


def test_validation_error_carries_source_row(spec_dir):
    # Map Date to a non-required-but-present field is fine; force a validation
    # error by mapping email to an int-typed model field is overkill — instead
    # drop the required signup_date mapping so the row fails validation.
    fp.Spec(
        signature=fp.compute_signature(["E-mail", "Name", "Date"]),
        mappings=[
            fp.Mapping(target="email", source="E-mail", transform="strip_lower"),
            fp.Mapping(target="full_name", source="Name", transform="strip"),
        ],
    ).save(spec_dir)
    rec = {"E-mail": "a@b.com", "Name": "Alice", "Date": "01.02.2026"}
    result = fp.Parser(User, spec_store=spec_dir, llm=None).parse([rec])
    assert result.errors
    assert result.errors[0].source_row == rec


# --------------------------------------------------------------------------- #
# quarantine_rows / write / read
# --------------------------------------------------------------------------- #


def test_quarantine_rows_shape(spec_dir):
    _user_spec(spec_dir)
    result = fp.Parser(User, spec_store=spec_dir, llm=None).parse(RECORDS)
    rows = quarantine_rows(result)
    assert len(rows) == 1
    row = rows[0]
    # Original fields preserved...
    assert row["E-mail"] == "bob@x.com" and row["Name"] == "Bob"
    # ...plus diagnostics.
    assert row[ROW_INDEX] == 1
    assert "transform" in row[ERROR_REASON]


def test_write_read_csv_roundtrip(spec_dir, tmp_path):
    _user_spec(spec_dir)
    result = fp.Parser(User, spec_store=spec_dir, llm=None).parse(RECORDS)
    path = tmp_path / "bad.csv"
    write_quarantine(result, path)

    text = path.read_text(encoding="utf-8")
    assert "_error_reason" in text and "bob@x.com" in text

    back = read_quarantine(path)
    assert back == [{"E-mail": "bob@x.com", "Name": "Bob", "Date": "not-a-date"}]


def test_write_read_json_roundtrip(spec_dir, tmp_path):
    _user_spec(spec_dir)
    result = fp.Parser(User, spec_store=spec_dir, llm=None).parse(RECORDS)
    path = tmp_path / "bad.json"
    write_quarantine(result, path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload[0]["_row_index"] == 1

    back = read_quarantine(path)
    assert back[0] == {"E-mail": "bob@x.com", "Name": "Bob", "Date": "not-a-date"}


# --------------------------------------------------------------------------- #
# Full loop: parse -> quarantine -> fix -> re-ingest
# --------------------------------------------------------------------------- #


def test_full_roundtrip_fix_and_reingest(spec_dir, tmp_path):
    _user_spec(spec_dir)
    parser = fp.Parser(User, spec_store=spec_dir, llm=None)

    first = parser.parse(RECORDS)
    assert len(first.valid_rows) == 1 and len(first.errors) == 1

    path = tmp_path / "bad.json"
    first.write_quarantine(path)

    # A human fixes the date; we read the file back and re-ingest the clean rows.
    fixed = read_quarantine(path)
    fixed[0]["Date"] = "15.03.2026"

    second = parser.parse(fixed)
    assert second.spec_generated is False  # same signature -> same spec, no drift
    assert second.errors == []
    assert second.valid_rows[0] == User(
        email="bob@x.com", full_name="Bob", signup_date=date(2026, 3, 15)
    )


# --------------------------------------------------------------------------- #
# CLI --quarantine
# --------------------------------------------------------------------------- #


def test_cli_quarantine_flag(spec_dir, tmp_path):
    _user_spec(spec_dir)
    src = tmp_path / "feed.csv"
    src.write_text(
        "E-mail;Name;Date\na@b.com;Alice;01.02.2026\nbob@x.com;Bob;not-a-date\n",
        encoding="utf-8",
    )
    qpath = tmp_path / "q.csv"
    code = cli.main(
        ["parse", str(src), "--model", "conftest:User", "--spec-dir", str(spec_dir), "--quarantine", str(qpath)]
    )
    assert code == cli.EXIT_FINDINGS
    assert qpath.exists()
    back = read_quarantine(qpath)
    assert back == [{"E-mail": "bob@x.com", "Name": "Bob", "Date": "not-a-date"}]
