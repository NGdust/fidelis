"""Validation of mapped records against the target Pydantic v2 model.

The result is always split: ``valid_rows`` (passed) and ``errors`` (failed, with
row number, field, and reason). Silently losing rows is not acceptable.
"""

from __future__ import annotations

import importlib
from typing import Iterable, Type

from pydantic import BaseModel, ValidationError

from .mapping import MappedRow
from .result import Coverage, RowError


def resolve_model(target: str | Type[BaseModel]) -> Type[BaseModel]:
    """Resolve the target model: a class or a dotted path ``app.models.User``."""

    if isinstance(target, type) and issubclass(target, BaseModel):
        return target
    if isinstance(target, str):
        module_path, _, attr = target.rpartition(".")
        if not module_path:
            raise ValueError(f"Invalid model path: {target!r}")
        module = importlib.import_module(module_path)
        model = getattr(module, attr)
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            raise TypeError(f"{target!r} is not a Pydantic BaseModel")
        return model
    raise TypeError(f"Unsupported target_model: {target!r}")


def _validation_errors(exc: ValidationError, mapped: MappedRow) -> list[RowError]:
    """Expand a Pydantic ValidationError into a list of per-field RowError."""

    errors: list[RowError] = []
    for err in exc.errors():
        loc = err.get("loc", ())
        field = ".".join(str(p) for p in loc) if loc else None
        raw = mapped.data.get(loc[0]) if loc else None
        errors.append(
            RowError(
                row_index=mapped.row_index,
                field=field,
                raw_value=raw,
                reason=err.get("msg", "validation error"),
                source_row=mapped.source,
            )
        )
    return errors or [
        RowError(
            mapped.row_index,
            None,
            mapped.data,
            "validation error",
            source_row=mapped.source,
        )
    ]


def validate_rows(
    model: Type[BaseModel],
    mapped_rows: Iterable[MappedRow],
    *,
    strict: bool = False,
) -> tuple[list[BaseModel], list[RowError], Coverage]:
    """Validate mapped records.

    Args:
        strict: When ``True``, the first error (transform or validation) raises an
            exception; when ``False``, it is collected into ``errors``.

    Returns:
        ``(valid_rows, errors, coverage)`` — coverage is computed by source row
        index, so it stays meaningful even when one row fans out into many.
    """

    valid: list[BaseModel] = []
    errors: list[RowError] = []
    seen: set = set()
    produced: set = set()
    errored: set = set()

    for mapped in mapped_rows:
        seen.add(mapped.row_index)
        if getattr(mapped, "empty", False):
            continue  # produced no record; counted in coverage denominator only
        if mapped.error is not None:
            errored.add(mapped.row_index)
            if strict:
                raise ValueError(str(mapped.error))
            errors.append(mapped.error)
            continue
        try:
            valid.append(model.model_validate(mapped.data))
            produced.add(mapped.row_index)
        except ValidationError as exc:
            errored.add(mapped.row_index)
            row_errors = _validation_errors(exc, mapped)
            if strict:
                raise ValueError(str(row_errors[0])) from exc
            errors.extend(row_errors)

    coverage = Coverage(
        rows_in=len(seen),
        rows_with_output=len(produced),
        rows_with_error=len(errored),
        records_out=len(valid),
    )
    return valid, errors, coverage
