"""Per-run context handed to transforms / enrichers / expanders.

Some hooks need run-time data, not just the cell: a synonyms table, a similarity
threshold, lookup dictionaries, config. ``Parser(context=...)`` makes that object
available to any hook that asks for it — a hook opts in simply by declaring a
``context`` parameter; existing hooks that don't are called exactly as before.

The active context is stored in a :class:`contextvars.ContextVar` set for the
duration of a single ``parse()`` (so concurrent parses with different contexts
don't interfere). Hook callers use :func:`call_hook`, which appends
``context=<ctx>`` only when the target accepts it.
"""

from __future__ import annotations

import contextvars
import inspect
from typing import Any, Callable

_run_context: contextvars.ContextVar = contextvars.ContextVar(
    "fidelis_run_context", default=None
)

#: Cache of fn -> whether it accepts a ``context`` argument.
_accepts: dict[Callable, bool] = {}


def set_context(ctx: Any):
    """Set the active run context; returns a token for :func:`reset_context`."""

    return _run_context.set(ctx)


def reset_context(token) -> None:
    _run_context.reset(token)


def get_context() -> Any:
    """The context for the current run (``None`` if none is set)."""

    return _run_context.get()


def accepts_context(fn: Callable) -> bool:
    """Whether ``fn`` declares a ``context`` parameter (or ``**kwargs``)."""

    cached = _accepts.get(fn)
    if cached is None:
        try:
            params = inspect.signature(fn).parameters
            cached = "context" in params or any(
                p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
            )
        except (TypeError, ValueError):  # builtins without a signature
            cached = False
        _accepts[fn] = cached
    return cached


def call_hook(fn: Callable, *args):
    """Call ``fn(*args)``, passing ``context=<run context>`` iff ``fn`` accepts it."""

    if accepts_context(fn):
        return fn(*args, context=_run_context.get())
    return fn(*args)
