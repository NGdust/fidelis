"""03 — expand: one source row fans out into several records.

    python examples/showcase/03_expand/run.py

Each route lists its airports in a single pipe-separated cell. A declarative
``expand`` step in the spec splits that cell so the feed yields one record per
airport — no code required. The separator lives in the spec, so a vendor that
uses ``;`` only needs a different spec.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))

from fidelis import Parser  # noqa: E402


class Leg(BaseModel):
    route: str
    airport: str


def main() -> None:
    result = Parser(Leg, spec_store=HERE, llm=None).parse(HERE / "data.csv")
    print(result.summary())
    for row in result.valid_rows:
        print(" ", row)


if __name__ == "__main__":
    main()
