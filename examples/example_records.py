"""example_records.py — the headline: ONE parser, TWO sources, SAME result.

"Feeds, not files." A file is just one adapter. The same ``parser.parse()``
consumes a CSV file today and a ``list[dict]`` (e.g. a webhook / API payload)
tomorrow, and — because both carry the same fields by meaning — they share a
single cached spec and produce identical ``valid_rows``.

Runs fully offline via ``FakeProvider`` (no API key, no network):

    python3 examples/example_records.py
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
    email: str
    full_name: str
    signup_date: date


ACME_MAPPINGS = [
    {"source": "E-mail", "target": "email", "transform": "strip_lower", "confidence": 0.98},
    {"source": "Name", "target": "full_name", "transform": "strip", "confidence": 0.91},
    {"source": "Date", "target": "signup_date", "transform": "parse_date:%d.%m.%Y", "confidence": 0.62},
]

# Same three users as acme_users.csv, but delivered as a list of dicts —
# note the differing key order/whitespace; field identity is order-insensitive.
RECORDS = [
    {"E-mail": " A@B.COM ", "Name": " Alice Anderson ", "Date": "01.02.2026"},
    {"Name": "Bob Brown", "Date": "15.03.2026", "E-mail": "bob@x.com"},
    {"E-mail": " CAROL@acme.io ", "Name": "Carol Clark", "Date": "28.06.2026"},
]


def main() -> None:
    provider = FakeProvider(mappings=ACME_MAPPINGS)

    with tempfile.TemporaryDirectory() as tmp:
        parser = Parser(target_model=User, spec_store=tmp, llm=provider)

        # 1) A CSV file on disk → spec generated once and cached.
        from_file = parser.parse(CSV_PATH)
        print("CSV file   :", from_file.summary())

        # 2) A list[dict] payload → SAME cached spec, 0 extra LLM calls.
        from_records = parser.parse(RECORDS)
        print("list[dict] :", from_records.summary())

        print("\nTotal LLM calls across both parses:", provider.call_count)

        same = from_file.valid_rows == from_records.valid_rows
        print("Identical valid_rows from both sources:", same)

        print("\nUsers (from the list[dict] payload):")
        for user in from_records.valid_rows:
            print("  ", user)


if __name__ == "__main__":
    main()
