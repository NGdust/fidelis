"""Input connectors: transparent gzip decompression and HTTP(S) URL fetching.

These run *before* adapter dispatch and turn a remote or compressed source into
in-memory bytes plus a resolved ``kind``, so the rest of the pipeline is
unchanged. A ``.csv.gz`` path or an ``https://…/feed.json`` URL just works:

    parser.parse("https://example.com/daily/feed.csv")
    parser.parse("exports/users.json.gz")

Only text/JSON feeds are handled here (the common remote formats). Excel is a
binary container that adapters read from a path/file, so Excel over URL/gzip
raises a clear error — download it first.
"""

from __future__ import annotations

import gzip
import os
from typing import Callable, Optional
from urllib.request import Request, urlopen

#: Extension → adapter kind. Mirrors the inference the Parser does for local files.
_EXT_KIND = {
    ".csv": "csv",
    ".tsv": "csv",
    ".txt": "csv",
    ".json": "json",
    ".xlsx": "excel",
    ".xls": "excel",
    ".xlsm": "excel",
}


def kind_from_name(name: str) -> Optional[str]:
    """Infer the adapter kind from a file name/URL path by extension."""

    lower = name.lower()
    for ext, kind in _EXT_KIND.items():
        if lower.endswith(ext):
            return kind
    return None


def is_url(source: object) -> bool:
    """Whether ``source`` is an HTTP(S) URL string."""

    return isinstance(source, str) and (
        source.startswith("http://") or source.startswith("https://")
    )


def _default_fetch(url: str, *, timeout: float = 30.0) -> bytes:
    req = Request(url, headers={"User-Agent": "fidelis"})
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310 - explicit http(s) only
        return resp.read()


#: Overridable fetcher (tests and callers can swap in their own client).
URL_FETCHER: Callable[[str], bytes] = _default_fetch


def _strip_query(url: str) -> str:
    return url.split("?", 1)[0].split("#", 1)[0]


def _reject_excel(kind: str, how: str) -> None:
    if kind == "excel":
        raise NotImplementedError(
            f"Excel over {how} is not supported — download the .xlsx file and "
            "parse it from a local path."
        )


def resolve_source(source: object, kind: Optional[str]) -> tuple[object, Optional[str]]:
    """Pre-process a source, returning ``(source, kind)`` for adapter dispatch.

    URL and ``.gz`` sources become decompressed in-memory bytes with a resolved
    ``kind``. Everything else passes through untouched.
    """

    if is_url(source):
        raw = URL_FETCHER(source)  # type: ignore[arg-type]
        path = _strip_query(source)  # type: ignore[arg-type]
        if path.lower().endswith(".gz"):
            raw = gzip.decompress(raw)
            path = path[:-3]
        resolved = kind or kind_from_name(path) or "csv"
        _reject_excel(resolved, "URL")
        return raw, resolved

    if isinstance(source, (str, os.PathLike)):
        name = os.fspath(source)
        if name.lower().endswith(".gz") and os.path.exists(name):
            with open(name, "rb") as fh:
                raw = gzip.decompress(fh.read())
            resolved = kind or kind_from_name(name[:-3]) or "csv"
            _reject_excel(resolved, "gzip")
            return raw, resolved

    return source, kind
