"""Stage 1: normalization of structural mess.

Works **only** for text/file sources (``raw_kind == "text"``): detection of
encoding, delimiter, and quote char, plus handling of ragged rows. Stage 1 does
not touch semantics (field names, types) — that is Stage 2's job.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Iterator, Optional

#: Encodings tried in order during autodetection.
_ENCODING_CANDIDATES = ("utf-8-sig", "utf-8", "cp1251", "latin-1")

#: Candidate delimiters for the CSV sniffer.
_DELIMITER_CANDIDATES = ",;\t|"


@dataclass
class DialectHints:
    """Result of detecting the structural dialect of a text source."""

    encoding: str
    delimiter: str
    quote_char: str = '"'


def detect_encoding(raw: bytes, override: Optional[str] = None) -> str:
    """Pick an encoding under which the bytes decode without errors."""

    if override:
        return override
    for enc in _ENCODING_CANDIDATES:
        try:
            raw.decode(enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue
    # latin-1 decodes any bytes — the final fallback.
    return "latin-1"


def detect_dialect(
    text: str,
    *,
    delimiter: Optional[str] = None,
    quote_char: Optional[str] = None,
) -> tuple[str, str]:
    """Determine the delimiter and quote char from a text sample.

    Explicitly passed values take priority over autodetection.
    """

    delim = delimiter
    quote = quote_char or '"'
    if delim is None:
        sample = "\n".join(text.splitlines()[:50])
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=_DELIMITER_CANDIDATES)
            delim = dialect.delimiter
            quote = quote_char or dialect.quotechar or '"'
        except csv.Error:
            # The sniffer failed — pick the delimiter with the highest frequency
            # in the first line, otherwise a comma.
            header = text.splitlines()[0] if text.splitlines() else ""
            delim = max(
                _DELIMITER_CANDIDATES,
                key=lambda d: header.count(d),
                default=",",
            )
            if header.count(delim) == 0:
                delim = ","
    return delim, quote


def normalize_text(
    raw: bytes | str,
    *,
    encoding: Optional[str] = None,
    delimiter: Optional[str] = None,
    quote_char: Optional[str] = None,
    skip_ragged: bool = False,
) -> tuple[list[dict], list[str], DialectHints]:
    """Turn raw CSV text into clean records with named fields.

    Args:
        raw: Bytes or string of the source file.
        encoding/delimiter/quote_char: Explicit values (from the spec) — disable
            the corresponding autodetection.
        skip_ragged: If ``True``, rows whose cell count does not match the header
            are skipped; otherwise they are normalized (trimmed / padded with
            ``None``) so that data is not silently lost.

    Returns:
        ``(records, field_names, hints)``.
    """

    if isinstance(raw, bytes):
        enc = detect_encoding(raw, encoding)
        text = raw.decode(enc)
    else:
        enc = encoding or "utf-8"
        text = raw

    delim, quote = detect_dialect(text, delimiter=delimiter, quote_char=quote_char)

    reader = csv.reader(io.StringIO(text), delimiter=delim, quotechar=quote)
    rows = list(reader)
    if not rows:
        return [], [], DialectHints(enc, delim, quote)

    header = [h.strip() for h in rows[0]]
    width = len(header)
    records: list[dict] = []
    for cells in rows[1:]:
        if not any(cell.strip() for cell in cells):
            continue  # completely empty row
        if len(cells) != width:
            if skip_ragged:
                continue
            # Ragged row: trim the excess / pad with None, losing nothing.
            cells = (cells + [None] * width)[:width]
        records.append(dict(zip(header, cells)))

    return records, header, DialectHints(enc, delim, quote)


def iter_normalized(records: list[dict]) -> Iterator[dict]:
    """A trivial lazy pass over already-normalized records."""

    yield from records
