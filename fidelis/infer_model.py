"""Infer a draft Pydantic model from a sample of a source.

When you don't have a target model yet, this lowers the barrier: point it at a
feed and it emits a ``BaseModel`` class, inferring a field name + type per
column from the sampled values (deterministic, no LLM). The output is a starting
point — review the types, rename fields, then use it as the ``Parser`` target.

    src = infer_model_source("feed.csv", class_name="User")
    print(src)  # ready-to-paste Python

Type inference per column (over the non-empty sample values): textual booleans →
``bool``; whole numbers → ``int``; decimals → ``float``; ISO/`dd.mm.yyyy` dates →
``date``; otherwise ``str``. A column with any empty/missing value becomes
``Optional[...] = None``.
"""

from __future__ import annotations

import keyword
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from .sources import from_csv, from_excel, from_json, from_records
from .sources.base import SourceData
from .sources.connectors import kind_from_name, resolve_source

# Textual booleans only — "0"/"1" are left to int inference so they aren't
# silently swallowed as bools.
_BOOL_TOKENS = {"true", "false", "yes", "no", "y", "n", "t", "f"}
_DATE_FORMATS = ("%d.%m.%Y", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y")


@dataclass
class InferredField:
    name: str
    type: str  # "int" | "float" | "bool" | "date" | "str"
    optional: bool
    source: str  # original source field name


# --------------------------------------------------------------------------- #
# Source loading (model-free)
# --------------------------------------------------------------------------- #


def _load(source: object, kind: Optional[str], sample_size: int) -> SourceData:
    source, kind = resolve_source(source, kind)
    if kind is None:
        if isinstance(source, (list, dict)):
            kind = "records"
        elif isinstance(source, (str, os.PathLike)):
            kind = kind_from_name(os.fspath(source)) or "csv"
        else:
            kind = "csv"
    if kind == "records":
        recs = [source] if isinstance(source, dict) else source
        return from_records(recs, sample_size=sample_size)
    if kind == "json":
        return from_json(source, sample_size=sample_size)
    if kind == "excel":
        return from_excel(source, sample_size=sample_size)
    if kind == "csv":
        return from_csv(source, sample_size=sample_size)
    raise ValueError(f"Unknown source kind: {kind!r}")


# --------------------------------------------------------------------------- #
# Field name + type inference
# --------------------------------------------------------------------------- #


def _to_identifier(name: str, taken: set[str]) -> str:
    """Turn a source field name into a unique, valid snake_case identifier."""

    ident = re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_").lower()
    ident = re.sub(r"_+", "_", ident)
    if not ident or not ident[0].isalpha():
        ident = f"field_{ident}" if ident else "field"
    if keyword.iskeyword(ident):
        ident += "_"
    base, n = ident, 2
    while ident in taken:
        ident = f"{base}_{n}"
        n += 1
    taken.add(ident)
    return ident


def _is_empty(value: object) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _is_int(text: str) -> bool:
    return bool(re.fullmatch(r"[+-]?\d+", text.replace(" ", "").replace(" ", "")))


def _is_float(text: str) -> bool:
    cleaned = text.replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        float(cleaned)
        return True
    except ValueError:
        return False


def _is_date(text: str) -> bool:
    try:
        date.fromisoformat(text)
        return True
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            datetime.strptime(text, fmt)
            return True
        except ValueError:
            continue
    return False


def _infer_type(values: list[object]) -> str:
    """Infer a column type from its non-empty sample values."""

    texts = [str(v).strip() for v in values if not _is_empty(v)]
    if not texts:
        return "str"
    if all(t.lower() in _BOOL_TOKENS for t in texts):
        return "bool"
    if all(_is_int(t) for t in texts):
        return "int"
    if all(_is_float(t) for t in texts):
        return "float"
    if all(_is_date(t) for t in texts):
        return "date"
    return "str"


def infer_fields(data: SourceData) -> list[InferredField]:
    """Infer one :class:`InferredField` per source column."""

    taken: set[str] = set()
    fields: list[InferredField] = []
    for source_name in data.field_names:
        values = [row.get(source_name) for row in data.sample]
        optional = any(source_name not in row or _is_empty(row.get(source_name)) for row in data.sample)
        fields.append(
            InferredField(
                name=_to_identifier(source_name, taken),
                type=_infer_type(values),
                optional=optional or not data.sample,
                source=source_name,
            )
        )
    return fields


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def render_model(class_name: str, fields: list[InferredField]) -> str:
    """Render inferred fields as importable Python source for a Pydantic model."""

    needs_date = any(f.type == "date" for f in fields)
    needs_optional = any(f.optional for f in fields)

    imports: list[str] = []
    if needs_date:
        imports.append("from datetime import date")
    if needs_optional:
        imports.append("from typing import Optional")
    imports.append("from pydantic import BaseModel")
    imports.append("")
    imports.append("")

    lines = [f"class {class_name}(BaseModel):"]
    if not fields:
        lines.append("    pass")
    for f in fields:
        annotated = f"Optional[{f.type}]" if f.optional else f.type
        default = " = None" if f.optional else ""
        comment = f"  # from {f.source!r}" if f.name != f.source else ""
        lines.append(f"    {f.name}: {annotated}{default}{comment}")

    return "\n".join(imports + lines) + "\n"


def infer_model_source(
    source: object,
    *,
    class_name: str = "Model",
    kind: Optional[str] = None,
    sample_size: int = 50,
) -> str:
    """Infer a model from ``source`` and return ready-to-paste Python source."""

    data = _load(source, kind, sample_size)
    return render_model(class_name, infer_fields(data))
