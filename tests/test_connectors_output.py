"""Tests for input connectors (gzip / URL) and typed ParseResult output.

- connectors.resolve_source: kind inference, gzip decompression (local + URL),
  HTTP(S) fetch via an overridable fetcher (no real network), Excel rejection.
- end-to-end: parser.parse() on a .csv.gz path and on a fake https:// URL.
- ParseResult.to_dicts / errors_to_dicts / to_pandas (polars via importorskip).
"""

from __future__ import annotations

import gzip
from datetime import date

import pytest

import fidelis as fp
from fidelis.sources import connectors as C

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


CSV_TEXT = "E-mail;Name;Date\na@b.com;Alice;01.02.2026\nbob@x.com;Bob;15.03.2026\n"
JSON_TEXT = '[{"E-mail":"a@b.com","Name":"Alice","Date":"01.02.2026"}]'


# --------------------------------------------------------------------------- #
# kind_from_name / is_url
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name,kind",
    [
        ("feed.csv", "csv"),
        ("feed.tsv", "csv"),
        ("feed.txt", "csv"),
        ("data.json", "json"),
        ("book.xlsx", "excel"),
        ("FEED.CSV", "csv"),
        ("noext", None),
    ],
)
def test_kind_from_name(name, kind):
    assert C.kind_from_name(name) == kind


def test_is_url():
    assert C.is_url("https://x/y.csv")
    assert C.is_url("http://x/y.csv")
    assert not C.is_url("/local/y.csv")
    assert not C.is_url(123)


# --------------------------------------------------------------------------- #
# resolve_source
# --------------------------------------------------------------------------- #


def test_resolve_passthrough_for_plain_path():
    assert C.resolve_source("feed.csv", None) == ("feed.csv", None)


def test_resolve_local_gzip(tmp_path):
    path = tmp_path / "feed.csv.gz"
    path.write_bytes(gzip.compress(CSV_TEXT.encode()))
    source, kind = C.resolve_source(str(path), None)
    assert kind == "csv"
    assert isinstance(source, bytes)
    assert b"Alice" in source


def test_resolve_url_fetches_and_infers_kind(monkeypatch):
    monkeypatch.setattr(C, "URL_FETCHER", lambda url: JSON_TEXT.encode())
    source, kind = C.resolve_source("https://example.com/data.json", None)
    assert kind == "json"
    assert source == JSON_TEXT.encode()


def test_resolve_url_with_query_string(monkeypatch):
    monkeypatch.setattr(C, "URL_FETCHER", lambda url: CSV_TEXT.encode())
    _source, kind = C.resolve_source("https://example.com/feed.csv?token=abc", None)
    assert kind == "csv"


def test_resolve_url_gzip(monkeypatch):
    monkeypatch.setattr(C, "URL_FETCHER", lambda url: gzip.compress(CSV_TEXT.encode()))
    source, kind = C.resolve_source("https://example.com/feed.csv.gz", None)
    assert kind == "csv"
    assert b"Alice" in source


def test_resolve_url_unknown_extension_defaults_csv(monkeypatch):
    monkeypatch.setattr(C, "URL_FETCHER", lambda url: CSV_TEXT.encode())
    _source, kind = C.resolve_source("https://example.com/export", None)
    assert kind == "csv"


def test_resolve_excel_over_url_rejected(monkeypatch):
    monkeypatch.setattr(C, "URL_FETCHER", lambda url: b"PK\x03\x04")
    with pytest.raises(NotImplementedError):
        C.resolve_source("https://example.com/book.xlsx", None)


# --------------------------------------------------------------------------- #
# End-to-end through the Parser
# --------------------------------------------------------------------------- #


def test_parse_gzip_csv_end_to_end(tmp_path, spec_dir):
    _user_spec(spec_dir)
    gz = tmp_path / "feed.csv.gz"
    gz.write_bytes(gzip.compress(CSV_TEXT.encode()))

    parser = fp.Parser(User, spec_store=spec_dir, llm=None)
    result = parser.parse(str(gz))
    assert len(result.valid_rows) == 2
    assert result.valid_rows[0] == User(
        email="a@b.com", full_name="Alice", signup_date=date(2026, 2, 1)
    )


def test_parse_url_csv_end_to_end(spec_dir, monkeypatch):
    _user_spec(spec_dir)
    monkeypatch.setattr(C, "URL_FETCHER", lambda url: CSV_TEXT.encode())

    parser = fp.Parser(User, spec_store=spec_dir, llm=None)
    result = parser.parse("https://example.com/daily/feed.csv")
    assert len(result.valid_rows) == 2
    assert result.valid_rows[1].full_name == "Bob"


# --------------------------------------------------------------------------- #
# Typed output
# --------------------------------------------------------------------------- #


def _parsed(spec_dir):
    _user_spec(spec_dir)
    parser = fp.Parser(User, spec_store=spec_dir, llm=None)
    return parser.parse(
        "E-mail;Name;Date\na@b.com;Alice;01.02.2026\nbad;Bob;not-a-date\n"
    )


def test_to_dicts_python_and_json_mode(spec_dir):
    result = _parsed(spec_dir)
    py = result.to_dicts()
    assert py[0]["signup_date"] == date(2026, 2, 1)
    js = result.to_dicts(mode="json")
    assert js[0]["signup_date"] == "2026-02-01"


def test_errors_to_dicts(spec_dir):
    result = _parsed(spec_dir)
    errs = result.errors_to_dicts()
    assert len(errs) == 1
    assert errs[0]["row_index"] == 1
    assert "field" in errs[0] and "reason" in errs[0]


def test_to_pandas(spec_dir):
    pd = pytest.importorskip("pandas")
    result = _parsed(spec_dir)
    df = result.to_pandas()
    assert list(df.columns) == ["email", "full_name", "signup_date"]
    assert len(df) == 1
    assert df.iloc[0]["email"] == "a@b.com"


def test_to_polars(spec_dir):
    pl = pytest.importorskip("polars")
    result = _parsed(spec_dir)
    df = result.to_polars()
    assert df.shape[0] == 1
    assert set(df.columns) == {"email", "full_name", "signup_date"}
