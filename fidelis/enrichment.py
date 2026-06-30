"""Post-mapping record enrichment + a registry for custom enrichers.

Where a transform sees a single field value, an enrichment sees the **whole
mapped record** AND the **original source record** — so it can derive new fields,
combine several columns (mapped or raw), mask values, or look things up in an
external source. Enrichers run after Stage 2 mapping and before Pydantic
validation, so any field they add is validated like the rest.

Register one from code with :func:`register_enrichment`, then reference it by
name in the spec (``enrich: [fill_full_name]``)::

    import fidelis

    @fidelis.register_enrichment("fill_full_name")
    def _full_name(record, source):
        # `source` is the raw input row — reach columns that aren't mapped.
        record["full_name"] = f"{source['First Name']} {source['Last Name']}".strip()
        return record
"""

from __future__ import annotations

from typing import Callable, Optional

#: Enrichment type: ``(record, source) -> record``. The function receives the
#: mapped record dict and the original source record, and may mutate the record
#: in place and/or return a (new) dict. Returning ``None`` means "I mutated the
#: record in place" — the same dict is used downstream.
EnrichmentFn = Callable[[dict, dict], Optional[dict]]

#: Batch enrichment type: ``list[record] -> list[record]``. The function receives
#: every successfully-mapped record at once (so it can do a single bulk lookup
#: instead of one call per row) and must return a list of the same length, in the
#: same order. Use it when a per-row enricher would make N external calls.
BatchEnrichmentFn = Callable[[list], list]


class EnrichmentError(ValueError):
    """An enrichment could not process the record."""


class BatchEnrichmentError(EnrichmentError):
    """A batch enrichment failed or broke the one-to-one row contract."""


_REGISTRY: dict[str, EnrichmentFn] = {}
_BATCH_REGISTRY: dict[str, BatchEnrichmentFn] = {}


def register_enrichment(
    name: str, fn: Optional[EnrichmentFn] = None, *, overwrite: bool = False
):
    """Register a custom enrichment under ``name``.

    Usable as a plain call (``register_enrichment("x", fn)``) or as a decorator
    (``@register_enrichment("x")``).
    """

    def _register(func: EnrichmentFn) -> EnrichmentFn:
        if name in _REGISTRY and not overwrite:
            raise ValueError(f"Enrichment {name!r} is already registered")
        _REGISTRY[name] = func
        return func

    # Decorator form: register_enrichment("x") returns the real decorator.
    if fn is None:
        return _register
    # Direct form: register_enrichment("x", fn).
    _register(fn)
    return None


def available_enrichments() -> list[str]:
    """List of names of registered enrichments."""

    return sorted(_REGISTRY)


def resolve_enrichment(ref: object) -> tuple[str, EnrichmentFn]:
    """Resolve an enrichment reference into a ``(name, fn)`` pair.

    ``ref`` is either a registered name (``str``) or a callable used directly.
    """

    if isinstance(ref, str):
        fn = _REGISTRY.get(ref)
        if fn is None:
            raise EnrichmentError(f"Unknown enrichment: {ref!r}")
        return ref, fn
    if callable(ref):
        return getattr(ref, "__name__", "<callable>"), ref
    raise EnrichmentError(
        f"Enrichment must be a registered name or a callable, got {type(ref)!r}"
    )


def apply_enrichment(fn: EnrichmentFn, record: dict, source: dict) -> dict:
    """Run a single enrichment and return the resulting record dict.

    ``record`` is the mapped target dict; ``source`` is the original input row.
    """

    from .runtime import call_hook

    result = call_hook(fn, record, source)
    if result is None:
        return record
    if not isinstance(result, dict):
        raise EnrichmentError(
            f"Enrichment must return a dict or None, got {type(result)!r}"
        )
    return result


# --------------------------------------------------------------------------- #
# Batch enrichment
# --------------------------------------------------------------------------- #


def register_batch_enrichment(
    name: str, fn: Optional[BatchEnrichmentFn] = None, *, overwrite: bool = False
):
    """Register a custom batch enrichment under ``name``.

    Usable as a plain call or as a decorator, exactly like
    :func:`register_enrichment`.
    """

    def _register(func: BatchEnrichmentFn) -> BatchEnrichmentFn:
        if name in _BATCH_REGISTRY and not overwrite:
            raise ValueError(f"Batch enrichment {name!r} is already registered")
        _BATCH_REGISTRY[name] = func
        return func

    if fn is None:
        return _register
    _register(fn)
    return None


def available_batch_enrichments() -> list[str]:
    """List of names of registered batch enrichments."""

    return sorted(_BATCH_REGISTRY)


def resolve_batch_enrichment(ref: object) -> tuple[str, BatchEnrichmentFn]:
    """Resolve a batch-enrichment reference into a ``(name, fn)`` pair."""

    if isinstance(ref, str):
        fn = _BATCH_REGISTRY.get(ref)
        if fn is None:
            raise BatchEnrichmentError(f"Unknown batch enrichment: {ref!r}")
        return ref, fn
    if callable(ref):
        return getattr(ref, "__name__", "<callable>"), ref
    raise BatchEnrichmentError(
        f"Batch enrichment must be a registered name or a callable, got {type(ref)!r}"
    )


def apply_batch_enrichment(fn: BatchEnrichmentFn, records: list) -> list:
    """Run one batch enrichment, enforcing the one-to-one row contract."""

    from .runtime import call_hook

    result = call_hook(fn, records)
    if not isinstance(result, list):
        raise BatchEnrichmentError(
            f"Batch enrichment must return a list, got {type(result)!r}"
        )
    if len(result) != len(records):
        raise BatchEnrichmentError(
            f"Batch enrichment must return {len(records)} record(s), got {len(result)}"
        )
    for i, rec in enumerate(result):
        if not isinstance(rec, dict):
            raise BatchEnrichmentError(
                f"Batch enrichment returned a non-dict at index {i}: {type(rec)!r}"
            )
    return result
