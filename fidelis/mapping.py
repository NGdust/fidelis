"""Stage 2: deterministic semantic mapping driven by the spec (0 LLM calls).

Applies the spec's mappings to source records: resolves source fields by their
normalized name (which is why a single spec works for both CSV and JSON), runs
the transforms, and assembles target dicts ready for Pydantic validation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from .enrichment import (
    BatchEnrichmentError,
    BatchEnrichmentFn,
    EnrichmentError,
    EnrichmentFn,
    apply_batch_enrichment,
    apply_enrichment,
)
from .expand import ExpanderFn, ExpansionError, apply_expander
from .result import RowError
from .spec import Mapping, Spec, normalize_field_name
from .transforms import TransformError, apply_transform


def _is_empty(value: object) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _resolve_value(m: Mapping, lookup: dict) -> object:
    """Compute a single mapping's target value: source→transform, with ``value``
    as a constant (no source) or a default (source empty/missing)."""

    if m.source is None:
        return m.value  # pure constant
    raw = lookup.get(normalize_field_name(m.source))
    result = apply_transform(m.transform, raw)
    if m.value is not None and _is_empty(result):
        return m.value  # default when the source cell is empty/missing
    return result


def _resolve_multi(m: Mapping, lookup: dict) -> dict:
    """Run a multi-target transform and return its output dict (``{}`` if empty)."""

    raw = lookup.get(normalize_field_name(m.source))
    if _is_empty(raw):
        return {}  # every target field becomes None
    result = apply_transform(m.transform, raw)
    if not isinstance(result, dict):
        raise TransformError(
            f"multi-target transform {m.transform!r} must return a dict, "
            f"got {type(result)!r}"
        )
    return result

#: An enricher as carried through mapping: a ``(name, fn)`` pair so a failure can
#: be attributed to a named enrichment in the resulting ``RowError``.
Enricher = tuple[str, EnrichmentFn]

#: A batch enricher carried through the pipeline: ``(name, fn)``.
BatchEnricher = tuple[str, BatchEnrichmentFn]

#: A row expander carried through the pipeline: ``(name, fn)``.
Expander = tuple[str, ExpanderFn]


@dataclass
class MappedRow:
    """A single mapped record before validation."""

    row_index: int
    data: dict
    error: Optional[RowError] = None
    #: The original source record (for quarantine / re-ingest).
    source: Optional[dict] = None
    #: A source row that produced no record at all (no rule fired / unpivot
    #: dropped everything). Counted in coverage's denominator, not validated.
    empty: bool = False


def _normalized_lookup(record: dict) -> dict:
    """Dict {normalized_field_name: value} for resolving source fields."""

    lookup: dict = {}
    for key, value in record.items():
        lookup[normalize_field_name(key)] = value
    return lookup


def unpivot_record(u, record: dict) -> list[dict]:
    """Expand repeating column groups into one record per index (pre-mapping)."""

    indices = u.indices
    group_cols = {
        tmpl.format(i=j) for tmpl in u.columns.values() for j in indices
    }
    rows: list[dict] = []
    for i in indices:
        new: dict = {}
        if u.keep_others:
            new = {k: v for k, v in record.items() if k not in group_cols}
        values = {canon: record.get(tmpl.format(i=i)) for canon, tmpl in u.columns.items()}
        if u.drop_empty and all(_is_empty(v) for v in values.values()):
            continue  # this index has no data — skip it
        new.update(values)
        if u.index_field:
            new[u.index_field] = i
        rows.append(new)
    return rows


_NUMERIC_OPS = {
    "gt": lambda a, b: a > b,
    "lt": lambda a, b: a < b,
    "ge": lambda a, b: a >= b,
    "le": lambda a, b: a <= b,
}


def _condition_matches(cond, lookup: dict) -> bool:
    """Evaluate a rule's ``when`` against the normalized source lookup."""

    raw = lookup.get(normalize_field_name(cond.field))
    if cond.op == "not_empty":
        return not _is_empty(raw)
    if cond.op == "empty":
        return _is_empty(raw)
    text = "" if raw is None else str(raw).strip()
    if cond.op == "eq":
        return text == str(cond.value)
    if cond.op == "ne":
        return text != str(cond.value)
    if cond.op == "in":
        return text in [str(v) for v in (cond.value or [])]
    try:
        return _NUMERIC_OPS[cond.op](float(text), float(cond.value))
    except (ValueError, TypeError):
        return False


def _map_with(
    mappings, record: dict, row_index: int, source_record: dict
) -> tuple[dict, Optional[RowError]]:
    """Apply a given list of mappings to a record."""

    lookup = _normalized_lookup(record)
    out: dict = {}
    for m in mappings:
        try:
            if m.targets:
                result = _resolve_multi(m, lookup)
                for out_key, field in m.targets.items():
                    out[field] = result.get(out_key)
            else:
                out[m.target] = _resolve_value(m, lookup)
        except (TransformError, ValueError, TypeError) as exc:
            err = RowError(
                row_index=row_index,
                field=m.target or ", ".join(m.target_fields),
                raw_value=lookup.get(normalize_field_name(m.source)) if m.source else None,
                reason=f"transform {m.transform!r} failed: {exc}",
                source_row=source_record,
            )
            return out, err
    return out, None


def _mapped_dict(
    spec: Spec, record: dict, row_index: int, source_record: Optional[dict] = None
) -> tuple[dict, Optional[RowError]]:
    """Map a record with the spec's base mappings (no rules)."""

    return _map_with(spec.mappings, record, row_index, source_record or record)


def _records_for(
    spec: Spec, sub: dict, row_index: int, source: dict
) -> list[tuple[dict, Optional[RowError]]]:
    """Produce the mapped record(s) for one (post-unpivot) source record.

    Without ``rules``: a single record. With ``rules``: one record per firing
    rule (base mappings + the rule's mappings); a row that fires no rule yields
    nothing."""

    if not spec.rules:
        return [_map_with(spec.mappings, sub, row_index, source)]
    lookup = _normalized_lookup(sub)
    out_rows: list[tuple[dict, Optional[RowError]]] = []
    for rule in spec.rules:
        if _condition_matches(rule.when, lookup):
            out_rows.append(
                _map_with(spec.mappings + rule.mappings, sub, row_index, source)
            )
    return out_rows


def _enrich_row(
    out: dict, record: dict, enrich: Sequence[Enricher], row_index: int
) -> tuple[dict, Optional[RowError]]:
    """Apply enrichers to one row, in order. Returns ``(out, error)``."""

    for name, fn in enrich:
        try:
            out = apply_enrichment(fn, out, record)
        except (EnrichmentError, ValueError, TypeError, KeyError) as exc:
            return out, RowError(
                row_index=row_index,
                field=None,
                raw_value=record,
                reason=f"enrichment {name!r} failed: {exc}",
                source_row=record,
            )
    return out, None


def map_record(
    spec: Spec,
    record: dict,
    row_index: int,
    enrich: Sequence[Enricher] = (),
) -> MappedRow:
    """Map + enrich a single record into one :class:`MappedRow`.

    A transform or enrichment failure marks the row with ``error`` (it becomes a
    ``RowError``) rather than being silently lost. Row expansion is a stream-level
    concern — see :func:`map_records`.
    """

    out, err = _mapped_dict(spec, record, row_index)
    if err is not None:
        return MappedRow(row_index=row_index, data=out, source=record, error=err)
    out, err = _enrich_row(out, record, enrich, row_index)
    return MappedRow(row_index=row_index, data=out, source=record, error=err)


def _expand_rows(
    base: dict, record: dict, expand: Sequence[Expander]
) -> list[dict]:
    """Fan ``base`` out through the expanders in order (flat-map). May raise
    :class:`ExpansionError`."""

    rows = [base]
    for name, fn in expand:
        try:
            rows = [r for row in rows for r in apply_expander(fn, row, record)]
        except ExpansionError:
            raise
        except (ValueError, TypeError, KeyError) as exc:
            raise ExpansionError(f"expansion {name!r} failed: {exc}") from exc
    return rows


def map_records(
    spec: Spec,
    records: Iterable[dict],
    enrich: Sequence[Enricher] = (),
    expand: Sequence[Expander] = (),
) -> Iterable[MappedRow]:
    """Lazily map a stream of source records.

    Pipeline per source row: ``unpivot`` (repeating column groups → many records)
    → map → ``expand`` (fan-out) → ``enrich`` (per fanned-out row). Every output
    row carries the source row's index so a failure still points back to the line.
    """

    for i, record in enumerate(records):
        if spec.skip_when:
            lk = _normalized_lookup(record)
            if any(_condition_matches(c, lk) for c in spec.skip_when):
                yield MappedRow(row_index=i, data={}, source=record, empty=True)
                continue
        produced: list[MappedRow] = []
        subs = unpivot_record(spec.unpivot, record) if spec.unpivot else [record]
        for sub in subs:
            for base, err in _records_for(spec, sub, i, record):
                if err is not None:
                    produced.append(MappedRow(row_index=i, data=base, source=record, error=err))
                    continue
                try:
                    rows = _expand_rows(base, record, expand)
                except ExpansionError as exc:
                    produced.append(MappedRow(
                        row_index=i,
                        data=base,
                        source=record,
                        error=RowError(i, None, record, str(exc), source_row=record),
                    ))
                    continue
                for row in rows:
                    enriched, eerr = _enrich_row(row, record, enrich, i)
                    produced.append(MappedRow(row_index=i, data=enriched, source=record, error=eerr))
        # A source row that produced nothing is still counted (coverage denominator).
        if not produced:
            produced.append(MappedRow(row_index=i, data={}, source=record, empty=True))
        yield from produced


def apply_column_steps(
    rows: list[MappedRow], steps: Sequence[tuple[str, str, object]]
) -> list[MappedRow]:
    """Rewrite whole columns over the clean rows (before validation).

    ``steps`` is a sequence of ``(field, name, fn)``. Each step gets that field's
    value across all clean rows and returns same-length rewritten values.
    """

    if not steps:
        return rows
    from .column import ColumnStepError, apply_column_step

    ok = [r for r in rows if r.error is None and not r.empty]
    for field, name, fn in steps:
        values = [r.data.get(field) for r in ok]
        try:
            rewritten = apply_column_step(fn, values)
        except ColumnStepError:
            raise
        except (ValueError, TypeError, KeyError) as exc:
            raise ColumnStepError(f"column step {name!r} failed: {exc}") from exc
        for r, v in zip(ok, rewritten):
            r.data[field] = v
    return rows


def apply_batch(
    rows: list[MappedRow], batch: Sequence[BatchEnricher]
) -> list[MappedRow]:
    """Apply batch enrichers to every successfully-mapped row at once.

    Rows that already carry a mapping/enrichment error are passed through
    untouched and excluded from the batch. The batch enrichers see only the
    clean records (in order), and their output is written back one-to-one — so a
    single bulk lookup can fill a field across the whole feed.

    Raises :class:`BatchEnrichmentError` if an enricher fails or breaks the
    one-to-one row contract; batch enrichment is all-or-nothing by nature.
    """

    if not batch:
        return rows

    ok_index = [i for i, r in enumerate(rows) if r.error is None]
    payload = [rows[i].data for i in ok_index]
    for name, fn in batch:
        try:
            payload = apply_batch_enrichment(fn, payload)
        except BatchEnrichmentError:
            raise
        except (EnrichmentError, ValueError, TypeError, KeyError) as exc:
            raise BatchEnrichmentError(
                f"batch enrichment {name!r} failed: {exc}"
            ) from exc

    for pos, i in enumerate(ok_index):
        rows[i].data = payload[pos]
    return rows
