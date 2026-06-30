"""04 — unpivot: repeating column groups become one record each.

    python examples/showcase/04_unpivot/run.py

The feed is "wide": each product carries three quarterly prices in separate
columns (Q1_PRICE, Q2_PRICE, Q3_PRICE). The spec's ``unpivot`` block turns
that into "tall" data — one record per (product, quarter) — before the normal
mappings run. The empty Q2 cell for Gadget is dropped (``drop_empty``).
"""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))

from fidelis import Parser  # noqa: E402


class Quote(BaseModel):
    product: str
    quarter: int
    price: float


def main() -> None:
    result = Parser(Quote, spec_store=HERE, llm=None).parse(HERE / "data.csv")
    print(result.summary())
    for row in result.valid_rows:
        print(" ", row)


if __name__ == "__main__":
    main()
