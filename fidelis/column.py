"""Whole-column stage (#4): decisions made over a column's distribution.

Some fixes can't be made one cell at a time — they depend on the whole column.
A **column step** receives every value of one (mapped) field as a list and
returns a same-length list of rewritten values, applied before validation.

    @fidelis.register_column_step("cents_if_huge")
    def cents_if_huge(values, context):
        nums = [v for v in values if isinstance(v, (int, float))]
        if nums and statistics.median(nums) > 10_000:      # looks like cents
            return [v / 100 if isinstance(v, (int, float)) else v for v in values]
        return values

    # in the spec:
    column_steps: {price: cents_if_huge}

Like other hooks, a step may declare a ``context`` parameter to receive
``Parser(context=...)``.
"""

from __future__ import annotations

from typing import Callable, Optional

#: Column step: ``list[value] -> list[value]`` (same length, same order).
ColumnStepFn = Callable[[list], list]


class ColumnStepError(ValueError):
    """A column step failed or broke the same-length contract."""


_REGISTRY: dict[str, ColumnStepFn] = {}


def register_column_step(
    name: str, fn: Optional[ColumnStepFn] = None, *, overwrite: bool = False
):
    """Register a column step under ``name`` (call or decorator form)."""

    def _register(func: ColumnStepFn) -> ColumnStepFn:
        if name in _REGISTRY and not overwrite:
            raise ValueError(f"Column step {name!r} is already registered")
        _REGISTRY[name] = func
        return func

    if fn is None:
        return _register
    _register(fn)
    return None


def available_column_steps() -> list[str]:
    """List of names of registered column steps."""

    return sorted(_REGISTRY)


def resolve_column_step(ref: object) -> tuple[str, ColumnStepFn]:
    """Resolve a column-step reference (name or callable) to ``(name, fn)``."""

    if isinstance(ref, str):
        fn = _REGISTRY.get(ref)
        if fn is None:
            raise ColumnStepError(f"Unknown column step: {ref!r}")
        return ref, fn
    if callable(ref):
        return getattr(ref, "__name__", "<callable>"), ref
    raise ColumnStepError(
        f"Column step must be a registered name or a callable, got {type(ref)!r}"
    )


def apply_column_step(fn: ColumnStepFn, values: list) -> list:
    """Run one column step, enforcing the same-length contract."""

    from .runtime import call_hook

    result = call_hook(fn, values)
    if not isinstance(result, list):
        raise ColumnStepError(
            f"Column step must return a list, got {type(result)!r}"
        )
    if len(result) != len(values):
        raise ColumnStepError(
            f"Column step must return {len(values)} value(s), got {len(result)}"
        )
    return result
