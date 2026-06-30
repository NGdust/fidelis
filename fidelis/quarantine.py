"""Quarantine round-trip — close the loop on rejected rows.

fidelis never drops a bad row silently; every failure is a :class:`RowError`
carrying the *original* source record. This module turns those into a file a
human can open and fix, then reads the fixed file back into clean records ready
to re-ingest:

    result = parser.parse("feed.csv")
    write_quarantine(result, "bad_rows.csv")     # hand off for a human to fix
    # …someone edits bad_rows.csv…
    fixed = read_quarantine("bad_rows.csv")      # diagnostic columns stripped
    again = parser.parse(fixed)                  # same signature → same spec

Each exported row is the original source record plus three diagnostic columns
(prefixed ``_`` so they are stripped on the way back in): ``_row_index``,
``_error_field``, ``_error_reason``.
"""

from __future__ import annotations

import csv
import json
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .result import ParseResult

_META_PREFIX = "_"
ROW_INDEX = "_row_index"
ERROR_FIELD = "_error_field"
ERROR_REASON = "_error_reason"


def quarantine_rows(result: "ParseResult") -> list[dict]:
    """Rejected rows as dicts: original source fields + diagnostic columns."""

    rows: list[dict] = []
    for err in result.errors:
        row = dict(err.source_row) if err.source_row else {}
        row[ROW_INDEX] = err.row_index
        row[ERROR_FIELD] = err.field or ""
        row[ERROR_REASON] = err.reason
        rows.append(row)
    return rows


def _ordered_fields(rows: list[dict]) -> list[str]:
    """Union of keys in first-appearance order, diagnostics pushed to the end."""

    data_fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key.startswith(_META_PREFIX):
                continue
            if key not in seen:
                seen.add(key)
                data_fields.append(key)
    return data_fields + [ROW_INDEX, ERROR_FIELD, ERROR_REASON]


def write_quarantine(result: "ParseResult", path: str | os.PathLike) -> str:
    """Write rejected rows to ``path`` (``.json`` → JSON, otherwise CSV)."""

    rows = quarantine_rows(result)
    name = os.fspath(path)
    if name.lower().endswith(".json"):
        with open(name, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=2, default=str)
    else:
        fields = _ordered_fields(rows)
        with open(name, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
    return name


def read_quarantine(path: str | os.PathLike) -> list[dict]:
    """Read a quarantine file back, stripping the ``_``-prefixed diagnostics.

    The result is a list of clean source records — feed it straight to
    ``parser.parse(rows)``.
    """

    name = os.fspath(path)
    if name.lower().endswith(".json"):
        with open(name, "r", encoding="utf-8") as fh:
            raw_rows = json.load(fh)
    else:
        with open(name, "r", newline="", encoding="utf-8") as fh:
            raw_rows = list(csv.DictReader(fh))
    return [
        {k: v for k, v in row.items() if not str(k).startswith(_META_PREFIX)}
        for row in raw_rows
    ]
