"""05 — rules: one row conditionally becomes several records.

    python examples/showcase/05_rules/run.py

Every transfer becomes a ``transfer`` ledger entry. When the row also carries
a non-empty FEE, a second ``fee`` entry is emitted from the same row. The
spec's ``rules`` block expresses that: each rule has a ``when`` predicate and
its own extra mappings, layered on top of the base mappings.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))

from fidelis import Parser  # noqa: E402


class Entry(BaseModel):
    txn: str
    kind: str
    amount: float


def main() -> None:
    result = Parser(Entry, spec_store=HERE, llm=None).parse(HERE / "data.csv")
    print(result.summary())
    for row in result.valid_rows:
        print(" ", row)


if __name__ == "__main__":
    main()
