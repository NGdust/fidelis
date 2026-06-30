"""Adapter for ``list[dict]`` — a structured input, Stage 1 is skipped."""

from __future__ import annotations

from typing import Iterable

from .base import (
    DEFAULT_SAMPLE_SIZE,
    SourceData,
    collect_field_names,
    materialize_sample,
)


def from_records(
    records: Iterable[dict], *, sample_size: int = DEFAULT_SAMPLE_SIZE
) -> SourceData:
    """Accept a ``list[dict]`` (or any iterable of dicts).

    Fields are collected as the union of keys across the sample — sources may
    yield heterogeneous records with missing values.
    """

    rows = list(records)
    field_names = collect_field_names(rows)
    sample, materialized = materialize_sample(rows, sample_size)
    return SourceData(
        records=materialized,
        field_names=field_names,
        sample=sample,
        raw_kind="structured",
        meta={"adapter": "records", "row_count": len(rows)},
    )
