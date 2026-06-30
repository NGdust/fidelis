"""Row expansion (fan-out): turn one input row into several records.

Sometimes a single source row stands for several entities — e.g. a line whose
"Airports" column lists ``"JFK|LAX|ORD"`` should become three rows, one per
airport. The common case is fully declarative in the spec — a column to split
and a delimiter — and needs **no code**::

    expand:
      - field: airport_code
        delimiter: "|"

For arbitrary fan-out logic you register a custom **expander** — a function that
takes the mapped record (and the original source row) and returns a *list* of
records — and reference it by name::

    @fidelis.register_expander("by_region")
    def by_region(record, source):
        return [{**record, "region": r} for r in lookup_regions(source["Airports"])]

    # in the spec:
    expand:
      - expander: by_region

Expanders run after mapping and before per-row enrichment, so each fanned-out
row is enriched, validated, and deduped on its own.
"""

from __future__ import annotations

from typing import Callable, Iterable, Optional

#: Expander type: ``(record, source) -> list[record]``. Receives the mapped
#: record and the original source row, and returns the records that replace it
#: (zero, one, or many).
ExpanderFn = Callable[[dict, dict], Iterable[dict]]


class ExpansionError(ValueError):
    """A row expander failed or returned something other than records."""


_REGISTRY: dict[str, ExpanderFn] = {}


def register_expander(
    name: str, fn: Optional[ExpanderFn] = None, *, overwrite: bool = False
):
    """Register a custom expander under ``name`` (call or decorator form)."""

    def _register(func: ExpanderFn) -> ExpanderFn:
        if name in _REGISTRY and not overwrite:
            raise ValueError(f"Expander {name!r} is already registered")
        _REGISTRY[name] = func
        return func

    if fn is None:
        return _register
    _register(fn)
    return None


def available_expanders() -> list[str]:
    """List of names of registered expanders."""

    return sorted(_REGISTRY)


def resolve_expander(ref: object) -> tuple[str, ExpanderFn]:
    """Resolve an expander reference (registered name or callable) to ``(name, fn)``."""

    if isinstance(ref, str):
        fn = _REGISTRY.get(ref)
        if fn is None:
            raise ExpansionError(f"Unknown expander: {ref!r}")
        return ref, fn
    if callable(ref):
        return getattr(ref, "__name__", "<callable>"), ref
    raise ExpansionError(
        f"Expander must be a registered name or a callable, got {type(ref)!r}"
    )


def apply_expander(fn: ExpanderFn, record: dict, source: dict) -> list[dict]:
    """Run one expander and return its records, validating the contract."""

    from .runtime import call_hook

    result = call_hook(fn, record, source)
    if result is None:
        raise ExpansionError("Expander must return an iterable of records, got None")
    rows = list(result)
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ExpansionError(
                f"Expander must yield dicts, got {type(row)!r} at index {i}"
            )
    return rows


def split_rows(
    field: str,
    delimiter: str = ",",
    *,
    strip: bool = True,
    drop_empty: bool = True,
) -> ExpanderFn:
    """Build an expander that splits ``record[field]`` into one row per value.

    This is what the declarative ``{field, delimiter}`` spec step uses under the
    hood; you can also register it directly. If the field is missing or splits to
    nothing, the row is left as-is (one row).
    """

    def _expand(record: dict, _source: dict) -> list[dict]:
        raw = record.get(field)
        if raw is None:
            return [record]
        parts = str(raw).split(delimiter)
        if strip:
            parts = [p.strip() for p in parts]
        if drop_empty:
            parts = [p for p in parts if p != ""]
        if not parts:
            return [record]
        return [{**record, field: p} for p in parts]

    return _expand
