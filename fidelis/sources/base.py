"""Unified internal representation of any data source.

A "file" is just one of the adapters, not the central entity. Every adapter
must return a standardized :class:`SourceData` object: lazy records, a field
inventory, and a small sample for schema inference.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Iterator, Literal

RawKind = Literal["text", "structured"]

#: How many records are sent to the LLM for schema inference by default.
DEFAULT_SAMPLE_SIZE = 20


@dataclass
class SourceData:
    """Standardized output of any input adapter.

    Attributes:
        records: Lazy source records (one ``dict`` per record).
        field_names: Inventory of source fields in order of appearance.
        sample: The first N records for schema inference.
        raw_kind: ``"text"`` — a normalization stage is required (Stage 1);
            ``"structured"`` — Stage 1 is skipped.
        meta: Arbitrary adapter metadata (file name, Excel sheet, etc.).
    """

    records: Iterable[dict]
    field_names: list[str]
    sample: list[dict]
    raw_kind: RawKind = "structured"
    meta: dict = field(default_factory=dict)

    def __iter__(self) -> Iterator[dict]:
        return iter(self.records)


def materialize_sample(
    records: Iterable[dict], sample_size: int = DEFAULT_SAMPLE_SIZE
) -> tuple[list[dict], Iterable[dict]]:
    """Take the first ``sample_size`` records without losing them for the main pass.

    Returns ``(sample, records)``. If the input is already a materialized
    sequence (list/tuple), ``records`` is returned as the full list, suitable
    for **repeated** iteration (no silent loss of rows on a second pass). For a
    lazy iterator, a one-shot "sample + tail" chain is built that preserves
    laziness.
    """

    if isinstance(records, (list, tuple)):
        rows = list(records)
        return rows[:sample_size], rows

    it = iter(records)
    sample: list[dict] = []
    for _ in range(sample_size):
        try:
            sample.append(next(it))
        except StopIteration:
            break

    def chain() -> Iterator[dict]:
        yield from sample
        yield from it

    return sample, chain()


def collect_field_names(rows: Iterable[dict]) -> list[str]:
    """Collect a field inventory from records, preserving first-appearance order."""

    seen: dict[str, None] = {}
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen[key] = None
    return list(seen.keys())
