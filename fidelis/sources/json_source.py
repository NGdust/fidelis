"""JSON adapter — a JSON array of objects. Structured input, no Stage 1."""

from __future__ import annotations

import json
import os
from typing import Union

from .base import DEFAULT_SAMPLE_SIZE, SourceData, collect_field_names, materialize_sample

JsonInput = Union[str, bytes, os.PathLike, list, dict]


def _load(source: JsonInput) -> list:
    """Load JSON from a path, a string/bytes, a ready list, or an object."""

    if isinstance(source, dict):
        return [source]
    if isinstance(source, list):
        return source
    if isinstance(source, (bytes, bytearray)):
        return json.loads(source.decode("utf-8"))
    if isinstance(source, (str, os.PathLike)):
        text = os.fspath(source)
        # Heuristic: read an existing path as a file, otherwise parse as a string.
        if isinstance(source, os.PathLike) or (
            "\n" not in text and len(text) < 4096 and os.path.exists(text)
        ):
            with open(text, "r", encoding="utf-8") as fh:
                return json.load(fh)
        return json.loads(text)
    raise TypeError(f"Unsupported JSON input: {type(source)!r}")


def from_json(
    source: JsonInput, *, sample_size: int = DEFAULT_SAMPLE_SIZE
) -> SourceData:
    """Accept a JSON array of objects (a path, string, bytes, or ``list``).

    A single object is also allowed — it is wrapped into a list of one record.
    """

    data = _load(source)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError("JSON source must be an array of objects or an object")
    rows = [r for r in data if isinstance(r, dict)]
    if len(rows) != len(data):
        raise ValueError("JSON array must contain only objects")

    field_names = collect_field_names(rows)
    sample, materialized = materialize_sample(rows, sample_size)
    return SourceData(
        records=materialized,
        field_names=field_names,
        sample=sample,
        raw_kind="structured",
        meta={"adapter": "json", "row_count": len(rows)},
    )
