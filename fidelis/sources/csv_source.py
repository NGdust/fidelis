"""CSV adapter — a text source that requires Stage 1 (normalization).

By default the adapter only reads the bytes and marks ``raw_kind="text"``,
deferring normalization to Stage 1 of the pipeline. But so that ``field_names``
and ``sample`` are available immediately (for the fingerprint and inference),
it performs a lightweight dialect detection here as well.
"""

from __future__ import annotations

import io
import os
from typing import Optional, Union

from ..normalize import normalize_text
from .base import DEFAULT_SAMPLE_SIZE, SourceData

PathOrBuffer = Union[str, os.PathLike, io.IOBase, bytes]


def _looks_like_path(text: str) -> bool:
    """Heuristic: a short, single-line string pointing to an existing file."""

    return "\n" not in text and len(text) < 4096 and os.path.exists(text)


def _read_bytes(path_or_buffer: PathOrBuffer) -> tuple[bytes, Optional[str]]:
    """Read raw bytes from a path, inline text, or a file object."""

    if isinstance(path_or_buffer, os.PathLike):
        name = os.fspath(path_or_buffer)
        with open(name, "rb") as fh:
            return fh.read(), name
    if isinstance(path_or_buffer, str):
        if _looks_like_path(path_or_buffer):
            with open(path_or_buffer, "rb") as fh:
                return fh.read(), path_or_buffer
        # Otherwise this is inline CSV text, not a path.
        return path_or_buffer.encode("utf-8"), None
    if isinstance(path_or_buffer, bytes):
        return path_or_buffer, None
    data = path_or_buffer.read()
    if isinstance(data, str):
        data = data.encode("utf-8")
    name = getattr(path_or_buffer, "name", None)
    return data, name


def from_csv(
    path_or_buffer: PathOrBuffer,
    *,
    encoding: Optional[str] = None,
    delimiter: Optional[str] = None,
    quote_char: Optional[str] = None,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
) -> SourceData:
    """Accept a CSV file, a file object, or raw bytes.

    The structural dialect (encoding/delimiter/quoting) is detected here;
    explicit arguments (usually from ``spec.parsing``) disable autodetection.
    """

    raw, name = _read_bytes(path_or_buffer)
    records, field_names, hints = normalize_text(
        raw,
        encoding=encoding,
        delimiter=delimiter,
        quote_char=quote_char,
    )
    sample = records[:sample_size]
    return SourceData(
        records=records,
        field_names=field_names,
        sample=sample,
        raw_kind="text",
        meta={
            "adapter": "csv",
            "source_name": name,
            "encoding": hints.encoding,
            "delimiter": hints.delimiter,
            "quote_char": hints.quote_char,
            "row_count": len(records),
        },
    )
