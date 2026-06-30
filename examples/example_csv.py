"""example_csv.py — parse a real CSV file with fidelis.

Runs fully offline: we inject ``FakeProvider`` so no API key and no network
are needed. Run it with:

    python3 examples/example_csv.py

The first ``parse()`` has no matching spec, so the (fake) LLM generates one and
writes it to ``spec_dir``. The second ``parse()`` reuses that cached spec and
performs ZERO LLM calls — the mapping is now deterministic.
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
CSV_PATH = HERE / "acme_users.csv"

# Make the example runnable without installing the package (repo root on path).
sys.path.insert(0, str(HERE.parent))

from fidelis import FakeProvider, Parser  # noqa: E402


class User(BaseModel):
    """The contract every incoming feed must satisfy."""

    email: str
    full_name: str
    signup_date: date


# What a real LLM would propose for an ``E-mail / Name / Date`` source.
# With FakeProvider we hand these mappings over directly — deterministic, offline.
ACME_MAPPINGS = [
    {"source": "E-mail", "target": "email", "transform": "strip_lower", "confidence": 0.98},
    {"source": "Name", "target": "full_name", "transform": "strip", "confidence": 0.91},
    # Low confidence (< 0.8) → automatically flagged needs_review in the spec.
    {"source": "Date", "target": "signup_date", "transform": "parse_date:%d.%m.%Y", "confidence": 0.62},
]


def main() -> None:
    provider = FakeProvider(mappings=ACME_MAPPINGS)

    with tempfile.TemporaryDirectory() as tmp:
        parser = Parser(
            target_model=User,
            spec_store=tmp,
            llm=provider,             # any LLMProvider or "anthropic:model" string
            on_unknown_column="error",
        )

        # --- First run: no spec yet → LLM generates and caches one. ---
        result = parser.parse(CSV_PATH)
        print("First run :", result.summary())
        print("  spec_generated:", result.spec_generated, "| LLM calls:", provider.call_count)
        print("  needs_review  :", result.needs_review, "(low-confidence date mapping)")

        # --- Second run: spec is cached → deterministic, 0 LLM calls. ---
        result2 = parser.parse(CSV_PATH)
        print("Second run:", result2.summary())
        print("  spec_generated:", result2.spec_generated, "| LLM calls:", provider.call_count)

        print("\nParsed users:")
        for user in result.valid_rows:
            print("  ", user)

        if result.errors:
            print("\nErrors:")
            for err in result.errors:
                print("  ", err)


if __name__ == "__main__":
    main()
