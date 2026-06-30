"""Tests for dedup/upsert keys (fidelis/dedup.py + Parser/CLI wiring)."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

import fidelis as fp
from fidelis import cli
from fidelis.dedup import dedup_rows
from fidelis.result import DuplicateRow

from conftest import User


class Row(BaseModel):
    email: str
    n: int


def _rows(*pairs):
    return [Row(email=e, n=n) for e, n in pairs]


# --------------------------------------------------------------------------- #
# dedup_rows unit
# --------------------------------------------------------------------------- #


def test_dedup_keeps_first_by_default():
    rows = _rows(("a", 1), ("b", 2), ("a", 3))
    kept, dups = dedup_rows(rows, ["email"], "first")
    assert [r.n for r in kept] == [1, 2]
    assert len(dups) == 1
    assert dups[0].key == {"email": "a"}
    assert dups[0].kept.n == 1 and dups[0].dropped.n == 3


def test_dedup_keep_last_replaces_in_place():
    rows = _rows(("a", 1), ("b", 2), ("a", 3))
    kept, dups = dedup_rows(rows, ["email"], "last")
    # Order preserved at the slot of the first occurrence, value is the last.
    assert [(r.email, r.n) for r in kept] == [("a", 3), ("b", 2)]
    assert dups[0].dropped.n == 1 and dups[0].kept.n == 3


def test_dedup_composite_key():
    class P(BaseModel):
        a: str
        b: str
        n: int

    rows = [P(a="x", b="1", n=1), P(a="x", b="2", n=2), P(a="x", b="1", n=3)]
    kept, dups = dedup_rows(rows, ["a", "b"], "first")
    assert [r.n for r in kept] == [1, 2]
    assert dups[0].key == {"a": "x", "b": "1"}


def test_dedup_no_duplicates():
    rows = _rows(("a", 1), ("b", 2))
    kept, dups = dedup_rows(rows, ["email"], "first")
    assert len(kept) == 2 and dups == []


def test_dedup_invalid_keep_raises():
    with pytest.raises(ValueError):
        dedup_rows(_rows(("a", 1)), ["email"], "middle")


# --------------------------------------------------------------------------- #
# Parser wiring
# --------------------------------------------------------------------------- #


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


def test_parser_dedup_by_email(spec_dir):
    _user_spec(spec_dir)
    parser = fp.Parser(User, spec_store=spec_dir, llm=None, dedup_key="email")
    # First and third rows collide on email after strip_lower.
    result = parser.parse([
        {"E-mail": "A@B.com", "Name": "Alice", "Date": "01.02.2026"},
        {"E-mail": "bob@x.com", "Name": "Bob", "Date": "15.03.2026"},
        {"E-mail": "a@b.com", "Name": "Alice2", "Date": "20.04.2026"},
    ])
    assert len(result.valid_rows) == 2
    assert len(result.duplicates) == 1
    assert result.duplicates[0].key == {"email": "a@b.com"}
    assert result.duplicates[0].kept.full_name == "Alice"  # first kept


def test_parser_dedup_keep_last(spec_dir):
    _user_spec(spec_dir)
    parser = fp.Parser(
        User, spec_store=spec_dir, llm=None, dedup_key="email", dedup_keep="last"
    )
    result = parser.parse([
        {"E-mail": "a@b.com", "Name": "Alice", "Date": "01.02.2026"},
        {"E-mail": "a@b.com", "Name": "Alice2", "Date": "20.04.2026"},
    ])
    assert len(result.valid_rows) == 1
    assert result.valid_rows[0].full_name == "Alice2"  # last won


def test_parser_unknown_dedup_field_raises(spec_dir):
    with pytest.raises(ValueError):
        fp.Parser(User, spec_store=spec_dir, llm=None, dedup_key="nonexistent")


def test_parser_invalid_keep_raises(spec_dir):
    with pytest.raises(ValueError):
        fp.Parser(User, spec_store=spec_dir, llm=None, dedup_key="email", dedup_keep="newest")


def test_no_dedup_key_leaves_duplicates(spec_dir):
    _user_spec(spec_dir)
    parser = fp.Parser(User, spec_store=spec_dir, llm=None)
    result = parser.parse([
        {"E-mail": "a@b.com", "Name": "Alice", "Date": "01.02.2026"},
        {"E-mail": "a@b.com", "Name": "Alice2", "Date": "20.04.2026"},
    ])
    assert len(result.valid_rows) == 2
    assert result.duplicates == []


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_cli_dedup_reports_count(spec_dir, tmp_path, capsys):
    _user_spec(spec_dir)
    src = tmp_path / "feed.csv"
    src.write_text(
        "E-mail;Name;Date\na@b.com;Alice;01.02.2026\na@b.com;Alice2;20.04.2026\n",
        encoding="utf-8",
    )
    code = cli.main(
        ["parse", str(src), "--model", "conftest:User", "--spec-dir", str(spec_dir),
         "--dedup-key", "email", "--json"]
    )
    report = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_OK
    assert report["valid"] == 1
    assert report["duplicates"] == 1
