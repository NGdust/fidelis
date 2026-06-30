"""Built-in value transforms + a registry for custom ones.

A transform is specified in the spec by a name (``strip``) or as ``name:arg``
(``parse_date:%d.%m.%Y``). Users can register their own transform from code via
:func:`register_transform` — as a plain call or as a decorator, like the
enrichment / expander / column-step registries::

    @fidelis.register_transform("upper")
    def _upper(value, arg):
        return str(value).upper()
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Callable, Optional

#: Transform type: (raw_value, arg) -> transformed_value. ``arg`` is the string
#: after the colon in the spec (``parse_date:%d.%m.%Y`` → arg == "%d.%m.%Y") or ``None``.
TransformFn = Callable[[object, Optional[str]], object]


class TransformError(ValueError):
    """The transform could not process the value."""


_REGISTRY: dict[str, TransformFn] = {}


def register_transform(
    name: str, fn: Optional[TransformFn] = None, *, overwrite: bool = False
):
    """Register a custom transform under ``name``.

    Usable as a plain call (``register_transform("x", fn)``) or as a decorator
    (``@register_transform("x")``).
    """

    def _register(func: TransformFn) -> TransformFn:
        if name in _REGISTRY and not overwrite:
            raise ValueError(f"Transform {name!r} is already registered")
        _REGISTRY[name] = func
        return func

    # Decorator form: register_transform("x") returns the real decorator.
    if fn is None:
        return _register
    # Direct form: register_transform("x", fn).
    _register(fn)
    return None


def builtin(name: str) -> Callable[[TransformFn], TransformFn]:
    """Decorator for registering a built-in transform."""

    def deco(fn: TransformFn) -> TransformFn:
        _REGISTRY[name] = fn
        return fn

    return deco


def available_transforms() -> list[str]:
    """List of names of registered transforms."""

    return sorted(_REGISTRY)


def parse_transform_spec(spec: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Parse a transform string into ``(name, arg)``.

    ``"parse_date:%d.%m.%Y"`` → ``("parse_date", "%d.%m.%Y")``;
    ``"strip"`` → ``("strip", None)``; ``None`` → ``(None, None)``.
    """

    if not spec:
        return None, None
    name, sep, arg = spec.partition(":")
    return name.strip(), (arg if sep else None)


def apply_transform(spec: Optional[str], value: object) -> object:
    """Apply a transform (given by the spec string) to a value.

    An empty value (``None`` / ``""``) passes straight through without a transform —
    whether the field is required is checked by Pydantic validation.
    """

    name, arg = parse_transform_spec(spec)
    if name is None:
        return value
    if name not in _REGISTRY:
        raise TransformError(f"Unknown transform: {name!r}")
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return value
    from .runtime import call_hook

    return call_hook(_REGISTRY[name], value, arg)


# --------------------------------------------------------------------------- #
# Built-in transforms
# --------------------------------------------------------------------------- #


@builtin("strip")
def _strip(value: object, _arg: Optional[str]) -> object:
    return value.strip() if isinstance(value, str) else value


@builtin("strip_lower")
def _strip_lower(value: object, _arg: Optional[str]) -> object:
    return value.strip().lower() if isinstance(value, str) else value


@builtin("to_int")
def _to_int(value: object, _arg: Optional[str]) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip().replace(" ", "").replace(" ", "")
    try:
        return int(text)
    except ValueError:
        # "12.0" → 12
        return int(float(text))


@builtin("to_float")
def _to_float(value: object, _arg: Optional[str]) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip().replace(" ", "").replace(" ", "").replace(",", ".")
    return float(text)


_TRUE = {"true", "1", "yes", "y", "t"}
_FALSE = {"false", "0", "no", "n", "f", ""}


@builtin("to_bool")
def _to_bool(value: object, _arg: Optional[str]) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise TransformError(f"Not a boolean value: {value!r}")


@builtin("clip")
def _clip(value: object, arg: Optional[str]) -> float:
    """Clamp a number into a range: ``clip:0:100`` (``clip:0:`` / ``clip::100`` ok)."""

    text = str(value).strip().replace(" ", "").replace(" ", "").replace(",", ".")
    num = float(text)
    lo, _sep, hi = (arg or "").partition(":")
    if lo:
        num = max(num, float(lo))
    if hi:
        num = min(num, float(hi))
    return num


@builtin("parse_date")
def _parse_date(value: object, arg: Optional[str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not arg:
        # No format given — try ISO.
        return date.fromisoformat(text)
    # One or more formats, pipe-separated, tried in order until one matches:
    #   parse_date:%Y-%m-%d|%d/%m/%Y|%d.%m.%Y
    formats = [fmt for fmt in arg.split("|") if fmt]
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise TransformError(
        f"date {text!r} matched none of the formats {formats}"
    )
