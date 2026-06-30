"""Deduplicate validated rows by a declared key.

Declare one or more model fields as the row key and fidelis collapses duplicate
rows within a feed, keeping the ``first`` or ``last`` occurrence. Dropped rows
are never lost silently — they are returned as :class:`DuplicateRow` records on
the result, each pairing the kept row with the one it displaced.
"""

from __future__ import annotations

from typing import Sequence

from pydantic import BaseModel

from .result import DuplicateRow


def _key_of(row: BaseModel, fields: Sequence[str]) -> tuple:
    """A hashable key tuple from the named fields of a validated row."""

    values = []
    for f in fields:
        v = getattr(row, f)
        try:
            hash(v)
        except TypeError:
            v = str(v)
        values.append(v)
    return tuple(values)


def dedup_rows(
    rows: list[BaseModel], fields: Sequence[str], keep: str = "first"
) -> tuple[list[BaseModel], list[DuplicateRow]]:
    """Collapse rows sharing a key.

    Args:
        rows: validated rows, in order.
        fields: model field names forming the key.
        keep: ``"first"`` (default) or ``"last"`` occurrence to retain.

    Returns ``(deduped_rows, duplicates)``; ``deduped_rows`` preserves order.
    """

    if keep not in ("first", "last"):
        raise ValueError(f"dedup_keep must be 'first' or 'last', got {keep!r}")

    fields = list(fields)
    result: list[BaseModel] = []
    pos: dict[tuple, int] = {}
    duplicates: list[DuplicateRow] = []

    for row in rows:
        key = _key_of(row, fields)
        if key not in pos:
            pos[key] = len(result)
            result.append(row)
            continue
        idx = pos[key]
        if keep == "last":
            # The new row wins; the previously kept one is the duplicate.
            dropped = result[idx]
            result[idx] = row
            duplicates.append(
                DuplicateRow(key=dict(zip(fields, key)), kept=row, dropped=dropped)
            )
        else:  # first
            duplicates.append(
                DuplicateRow(key=dict(zip(fields, key)), kept=result[idx], dropped=row)
            )

    return result, duplicates
