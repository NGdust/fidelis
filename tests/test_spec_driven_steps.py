"""Tests for spec-driven record steps: enrich / batch_enrich / dedup in the spec.

The spec is the contract — these steps now live in the YAML and are referenced
by registered name (like transforms). Parser-level args remain a global default
for specs that don't declare a step. Covered:
- Spec round-trips the new sections through YAML;
- a spec's enrich/batch_enrich/dedup are applied at parse time;
- spec steps take precedence over Parser defaults; Parser default applies when
  the spec is silent;
- validate_spec flags unknown enrich/batch names and bad dedup keys;
- unknown names in a spec fail loudly at parse.
"""

from __future__ import annotations

import pytest

import fidelis as fp
from fidelis import enrichment as E
from fidelis.enrichment import EnrichmentError
from fidelis.spec import DedupSpec

from conftest import User


@pytest.fixture(autouse=True)
def _restore_registries():
    snap, bsnap = dict(E._REGISTRY), dict(E._BATCH_REGISTRY)
    try:
        yield
    finally:
        E._REGISTRY.clear()
        E._REGISTRY.update(snap)
        E._BATCH_REGISTRY.clear()
        E._BATCH_REGISTRY.update(bsnap)


def _spec(spec_dir, **extra):
    spec = fp.Spec(
        signature=fp.compute_signature(["E-mail", "Name", "Date"]),
        mappings=[
            fp.Mapping(target="email", source="E-mail", transform="strip_lower"),
            fp.Mapping(target="full_name", source="Name", transform="strip"),
            fp.Mapping(
                target="signup_date", source="Date", transform="parse_date:%d.%m.%Y"
            ),
        ],
        **extra,
    )
    spec.save(spec_dir)
    return spec


RECS = [
    {"E-mail": "a@b.com", "Name": "Alice", "Date": "01.02.2026"},
    {"E-mail": "a@b.com", "Name": "Alice2", "Date": "20.04.2026"},
    {"E-mail": "bob@x.com", "Name": "Bob", "Date": "15.03.2026"},
]


# --------------------------------------------------------------------------- #
# YAML round-trip
# --------------------------------------------------------------------------- #


def test_spec_roundtrips_new_sections(tmp_path):
    spec = fp.Spec(
        signature="abc123",
        mappings=[fp.Mapping(target="email", source="E-mail")],
        enrich=["fill_domain"],
        batch_enrich=["attach_scores"],
        dedup=DedupSpec(key=["email"], keep="last"),
    )
    text = spec.dump_yaml()
    assert "enrich:" in text and "fill_domain" in text
    assert "batch_enrich:" in text and "attach_scores" in text
    assert "dedup:" in text

    back = fp.Spec.from_yaml(text)
    assert back.enrich == ["fill_domain"]
    assert back.batch_enrich == ["attach_scores"]
    assert back.dedup.key == ["email"] and back.dedup.keep == "last"


def test_spec_omits_empty_sections(tmp_path):
    spec = fp.Spec(
        signature="abc123",
        mappings=[fp.Mapping(target="email", source="E-mail")],
    )
    text = spec.dump_yaml()
    assert "enrich" not in text
    assert "batch_enrich" not in text
    assert "dedup" not in text


# --------------------------------------------------------------------------- #
# Spec-driven application
# --------------------------------------------------------------------------- #


def test_spec_enrich_applied(spec_dir):
    _spec(spec_dir, enrich=["shout"])
    fp.register_enrichment("shout", lambda r, s: {**r, "full_name": r["full_name"].upper()})

    result = fp.Parser(User, spec_store=spec_dir, llm=None).parse([RECS[0]])
    assert result.valid_rows[0].full_name == "ALICE"


def test_spec_batch_enrich_applied(spec_dir):
    _spec(spec_dir, batch_enrich=["tag"])

    @fp.register_batch_enrichment("tag")
    def tag(records):
        for r in records:
            r["full_name"] = "X"
        return records

    result = fp.Parser(User, spec_store=spec_dir, llm=None).parse([RECS[0]])
    assert result.valid_rows[0].full_name == "X"


def test_spec_dedup_applied(spec_dir):
    _spec(spec_dir, dedup=DedupSpec(key=["email"], keep="first"))
    result = fp.Parser(User, spec_store=spec_dir, llm=None).parse(RECS)
    assert len(result.valid_rows) == 2
    assert len(result.duplicates) == 1
    assert result.valid_rows[0].full_name == "Alice"  # first kept


def test_spec_dedup_keep_last(spec_dir):
    _spec(spec_dir, dedup=DedupSpec(key=["email"], keep="last"))
    result = fp.Parser(User, spec_store=spec_dir, llm=None).parse(RECS)
    assert len(result.valid_rows) == 2
    # The kept a@b.com row is the last occurrence.
    emails = {r.email: r.full_name for r in result.valid_rows}
    assert emails["a@b.com"] == "Alice2"


# --------------------------------------------------------------------------- #
# Precedence: spec over Parser default
# --------------------------------------------------------------------------- #


def test_spec_enrich_overrides_parser_default(spec_dir):
    _spec(spec_dir, enrich=["from_spec"])
    fp.register_enrichment("from_spec", lambda r, s: {**r, "full_name": "SPEC"})
    fp.register_enrichment("from_parser", lambda r, s: {**r, "full_name": "PARSER"})

    # Parser default says 'from_parser', but the spec declares 'from_spec' → spec wins.
    parser = fp.Parser(User, spec_store=spec_dir, llm=None, enrich=["from_parser"])
    result = parser.parse([RECS[0]])
    assert result.valid_rows[0].full_name == "SPEC"


def test_parser_default_used_when_spec_silent(spec_dir):
    _spec(spec_dir)  # no enrich in the spec
    fp.register_enrichment("from_parser", lambda r, s: {**r, "full_name": "PARSER"})

    parser = fp.Parser(User, spec_store=spec_dir, llm=None, enrich=["from_parser"])
    result = parser.parse([RECS[0]])
    assert result.valid_rows[0].full_name == "PARSER"


def test_spec_dedup_overrides_parser_default(spec_dir):
    _spec(spec_dir, dedup=DedupSpec(key=["email"], keep="last"))
    # Parser default keep=first; spec says last → spec wins.
    parser = fp.Parser(User, spec_store=spec_dir, llm=None, dedup_key="email", dedup_keep="first")
    result = parser.parse(RECS)
    emails = {r.email: r.full_name for r in result.valid_rows}
    assert emails["a@b.com"] == "Alice2"  # last, from spec


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def test_validate_spec_flags_unknown_enrich(spec_dir):
    spec = _spec(spec_dir, enrich=["does_not_exist"])
    problems = fp.Parser(User, spec_store=spec_dir, llm=None).validate_spec(spec)
    assert any("does_not_exist" in p for p in problems)


def test_validate_spec_flags_unknown_batch(spec_dir):
    spec = _spec(spec_dir, batch_enrich=["nope_batch"])
    problems = fp.Parser(User, spec_store=spec_dir, llm=None).validate_spec(spec)
    assert any("nope_batch" in p for p in problems)


def test_validate_spec_flags_bad_dedup_key(spec_dir):
    spec = _spec(spec_dir, dedup=DedupSpec(key=["not_a_field"]))
    problems = fp.Parser(User, spec_store=spec_dir, llm=None).validate_spec(spec)
    assert any("not_a_field" in p for p in problems)


def test_validate_spec_accepts_registered_steps(spec_dir):
    fp.register_enrichment("ok_enrich", lambda r, s: r)
    spec = _spec(spec_dir, enrich=["ok_enrich"], dedup=DedupSpec(key=["email"]))
    problems = fp.Parser(User, spec_store=spec_dir, llm=None).validate_spec(spec)
    assert problems == []


def test_unknown_spec_enrich_raises_at_parse(spec_dir):
    _spec(spec_dir, enrich=["ghost"])
    with pytest.raises(EnrichmentError):
        fp.Parser(User, spec_store=spec_dir, llm=None).parse([RECS[0]])
