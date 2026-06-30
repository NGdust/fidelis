"""FakeProvider — an injectable provider for tests and offline flows.

Makes no network calls. Accepts either a ready-made response string or a callable
that returns a string for a (system, user) pair. All of the PRD's acceptance
criteria are checked against it.
"""

from __future__ import annotations

import json
from typing import Callable, Optional, Union

from .base import LLMProvider

Responder = Union[str, Callable[[str, str], str]]


class FakeProvider(LLMProvider):
    """Deterministic provider for tests.

    Examples:
        >>> FakeProvider(response='{"mappings": []}')
        >>> FakeProvider(responder=lambda sys, user: my_json)
    """

    def __init__(
        self,
        model: str = "fake",
        *,
        response: Optional[str] = None,
        responder: Optional[Responder] = None,
        mappings: Optional[list[dict]] = None,
        parsing: Optional[dict] = None,
    ):
        self.model = model
        self.calls: list[tuple[str, str]] = []
        if mappings is not None:
            payload: dict = {"mappings": mappings}
            if parsing is not None:
                payload["parsing"] = parsing
            self._responder: Responder = json.dumps(payload, ensure_ascii=False)
        elif responder is not None:
            self._responder = responder
        elif response is not None:
            self._responder = response
        else:
            self._responder = '{"mappings": []}'

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if callable(self._responder):
            return self._responder(system, user)
        return self._responder
