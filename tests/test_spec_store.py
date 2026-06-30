"""Tests for pluggable spec storage (fidelis/store.py + Parser wiring).

- MemorySpecStore / FileSpecStore implement the interface;
- Parser(spec_store=...) reads, drift-detects, and writes through the store
  instead of the filesystem;
- a generated spec is saved back to the store;
- a custom store's get/save are actually called.
"""

from __future__ import annotations

from datetime import date

import pytest

import fidelis as fp
from fidelis import FileSpecStore, MemorySpecStore, SpecStore

from conftest import USER_MAPPINGS, User


def _user_spec():
    return fp.Spec(
        signature=fp.compute_signature(["E-mail", "Name", "Date"]),
        mappings=[
            fp.Mapping(target="email", source="E-mail", transform="strip_lower"),
            fp.Mapping(target="full_name", source="Name", transform="strip"),
            fp.Mapping(target="signup_date", source="Date", transform="parse_date:%d.%m.%Y"),
        ],
    )


RECS = [
    {"E-mail": "a@b.com", "Name": "Alice", "Date": "01.02.2026"},
    {"E-mail": "bob@x.com", "Name": "Bob", "Date": "15.03.2026"},
]


# --------------------------------------------------------------------------- #
# MemorySpecStore
# --------------------------------------------------------------------------- #


def test_memory_store_get_save_all():
    store = MemorySpecStore()
    assert store.get("nope") is None
    spec = _user_spec()
    store.save(spec)
    assert store.get(spec.signature) is spec
    assert list(store.all()) == [spec]


def test_memory_store_seeded():
    spec = _user_spec()
    store = MemorySpecStore([spec])
    assert store.get(spec.signature) is spec


def test_parser_uses_memory_store_no_llm():
    store = MemorySpecStore([_user_spec()])
    parser = fp.Parser(User, spec_store=store, llm=None)
    result = parser.parse(RECS)
    assert result.spec_generated is False
    assert result.valid_rows[0] == User(
        email="a@b.com", full_name="Alice", signup_date=date(2026, 2, 1)
    )


def test_generated_spec_is_saved_to_store():
    store = MemorySpecStore()
    provider = fp.FakeProvider(mappings=USER_MAPPINGS)
    parser = fp.Parser(User, spec_store=store, llm=provider)

    result = parser.parse(RECS)
    assert result.spec_generated is True
    # The new spec is now in the store, keyed by signature.
    sig = fp.compute_signature(["E-mail", "Name", "Date"])
    assert store.get(sig) is not None

    # Second parse: served from the store, no extra LLM call.
    parser.parse(RECS)
    assert provider.call_count == 1


def test_store_drift_detection():
    store = MemorySpecStore([_user_spec()])
    parser = fp.Parser(User, spec_store=store, llm=None, on_unknown_column="ignore")
    # Same source + an extra column → not an exact signature match, near-match drift.
    result = parser.parse([{**RECS[0], "Phone": "123"}])
    assert result.drift_report.has_drift
    assert "Phone" in result.drift_report.new_fields


# --------------------------------------------------------------------------- #
# FileSpecStore parity
# --------------------------------------------------------------------------- #


def test_file_store_roundtrip(tmp_path):
    store = FileSpecStore(tmp_path)
    spec = _user_spec()
    store.save(spec)
    assert (tmp_path / f"spec_{spec.signature}.yaml").exists()
    loaded = store.get(spec.signature)
    assert loaded is not None and loaded.signature == spec.signature


def test_parser_path_builds_file_store(spec_dir):
    # A path passed as spec_store is sugar for a FileSpecStore.
    _user_spec().save(spec_dir)
    parser = fp.Parser(User, spec_store=spec_dir, llm=None)
    assert isinstance(parser._store, FileSpecStore)
    result = parser.parse(RECS)
    assert len(result.valid_rows) == 2


# --------------------------------------------------------------------------- #
# Custom store is actually used
# --------------------------------------------------------------------------- #


def test_custom_store_get_and_save_are_called():
    calls = {"get": 0, "save": 0}

    class SpyStore(SpecStore):
        def __init__(self):
            self._d = {}

        def get(self, signature):
            calls["get"] += 1
            return self._d.get(signature)

        def save(self, spec):
            calls["save"] += 1
            self._d[spec.signature] = spec

    store = SpyStore()
    provider = fp.FakeProvider(mappings=USER_MAPPINGS)
    parser = fp.Parser(User, spec_store=store, llm=provider)

    parser.parse(RECS)          # get -> miss -> generate -> save
    parser.parse(RECS)          # get -> hit
    assert calls["get"] == 2
    assert calls["save"] == 1


def test_store_without_all_skips_drift():
    # A store that only implements get/save (no all) → no drift candidate, a fresh
    # spec is generated instead of raising drift.
    class MinimalStore(SpecStore):
        def __init__(self):
            self._d = {}

        def get(self, signature):
            return self._d.get(signature)

        def save(self, spec):
            self._d[spec.signature] = spec

    provider = fp.FakeProvider(mappings=USER_MAPPINGS)
    parser = fp.Parser(User, spec_store=MinimalStore(), llm=provider)
    parser.parse(RECS)
    # A different-shaped source: no `all()` → no drift candidate → generate anew.
    result = parser.parse([{**RECS[0], "Phone": "1"}])
    assert result.spec_generated is True
    assert result.drift_report.has_drift is False
