"""Tests for run context injection (#5): Parser(context=...).

A hook opts in by declaring a ``context`` parameter; hooks that don't are called
exactly as before. Context flows to transforms, enrichers, batch enrichers, and
expanders, and is isolated per parse() (a ContextVar).
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

import fidelis as fp
from fidelis import enrichment as E
from fidelis import expand as X
from fidelis import transforms as T
from fidelis.runtime import accepts_context, call_hook


@pytest.fixture(autouse=True)
def _restore_registries():
    snaps = [
        (T._REGISTRY, dict(T._REGISTRY)),
        (E._REGISTRY, dict(E._REGISTRY)),
        (E._BATCH_REGISTRY, dict(E._BATCH_REGISTRY)),
        (X._REGISTRY, dict(X._REGISTRY)),
    ]
    try:
        yield
    finally:
        for reg, snap in snaps:
            reg.clear()
            reg.update(snap)


# --------------------------------------------------------------------------- #
# runtime helpers
# --------------------------------------------------------------------------- #


def test_accepts_context_detection():
    assert accepts_context(lambda v, a, context: v) is True
    assert accepts_context(lambda v, a, **kw: v) is True
    assert accepts_context(lambda v, a: v) is False


def test_call_hook_without_context_set_passes_none():
    seen = {}

    def hook(v, a, context):
        seen["ctx"] = context
        return v

    call_hook(hook, 1, None)        # no context set -> None
    assert seen["ctx"] is None


# --------------------------------------------------------------------------- #
# context reaches each kind of hook
# --------------------------------------------------------------------------- #


class Supplier(BaseModel):
    name: str


def _spec(spec_dir, **extra):
    fp.Spec(
        signature=fp.compute_signature(["Supplier"]),
        mappings=[fp.Mapping(target="name", source="Supplier", transform="canon")],
        **extra,
    ).save(spec_dir)


def test_context_reaches_transform(spec_dir):
    # The #5 example: canonicalize a supplier name using a synonyms table + a
    # threshold passed in as run context, not hardcoded.
    fp.register_transform(
        "canon",
        lambda value, arg, context: context["synonyms"].get(str(value).strip(), value),
    )
    _spec(spec_dir)

    ctx = {"synonyms": {"BP Plc": "BP Aviation"}, "threshold": 0.85}
    parser = fp.Parser(Supplier, spec_store=spec_dir, llm=None, context=ctx)
    result = parser.parse([{"Supplier": "BP Plc"}])
    assert result.valid_rows[0].name == "BP Aviation"


def test_context_reaches_enricher(spec_dir):
    fp.register_transform("canon", lambda v, a: str(v).strip())  # plain, no ctx
    _spec(spec_dir, enrich=["tag_region"])

    @fp.register_enrichment("tag_region")
    def tag_region(record, source, context):
        record["name"] = f"{record['name']}/{context['region']}"
        return record

    parser = fp.Parser(Supplier, spec_store=spec_dir, llm=None, context={"region": "EU"})
    result = parser.parse([{"Supplier": "BP"}])
    assert result.valid_rows[0].name == "BP/EU"


def test_context_reaches_batch_enricher(spec_dir):
    fp.register_transform("canon", lambda v, a: str(v).strip())
    _spec(spec_dir, batch_enrich=["suffix"])

    @fp.register_batch_enrichment("suffix")
    def suffix(records, context):
        for r in records:
            r["name"] = r["name"] + context["suffix"]
        return records

    parser = fp.Parser(Supplier, spec_store=spec_dir, llm=None, context={"suffix": "!"})
    result = parser.parse([{"Supplier": "BP"}])
    assert result.valid_rows[0].name == "BP!"


def test_context_reaches_expander(spec_dir):
    fp.register_transform("canon", lambda v, a: str(v).strip())
    _spec(spec_dir, expand=[fp.ExpandStep(expander="by_sep")])

    @fp.register_expander("by_sep")
    def by_sep(record, source, context):
        return [{**record, "name": p} for p in record["name"].split(context["sep"])]

    parser = fp.Parser(Supplier, spec_store=spec_dir, llm=None, context={"sep": "+"})
    result = parser.parse([{"Supplier": "A+B+C"}])
    assert {r.name for r in result.valid_rows} == {"A", "B", "C"}


def test_no_context_means_existing_hooks_unaffected(spec_dir):
    # A plain transform without a context param still works (no context set).
    fp.register_transform("canon", lambda v, a: str(v).strip().upper())
    _spec(spec_dir)
    parser = fp.Parser(Supplier, spec_store=spec_dir, llm=None)  # no context
    result = parser.parse([{"Supplier": " bp "}])
    assert result.valid_rows[0].name == "BP"
