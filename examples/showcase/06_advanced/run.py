"""06 — advanced: the whole pipeline on one messy vendor feed.

    python examples/showcase/06_advanced/run.py

A single dirty fuel-price file exercises most of the library at once:

- ``skip_when``        drops the vendor's TOTAL footer row;
- a multi-target       transform splits "JFK / John F Kennedy" into code + name;
- ``parse_date``       accepts both 2026-01-15 and 15/02/2026;
- ``clip``             clamps an absurd 99999 price into a sane ceiling;
- a column step        spots volumes reported in millilitres (whole-column
                       decision) and rescales them — using the per-run
                       ``context`` for the divisor;
- the row with a       non-numeric price fails validation and is quarantined,
                       so ``coverage`` drops below 1.0 and reports what was lost.
"""

from __future__ import annotations

import statistics
import sys
from datetime import date
from pathlib import Path

from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))

import fidelis  # noqa: E402
from fidelis import Parser  # noqa: E402


class Quote(BaseModel):
    airport: str = Field(min_length=1)
    airport_name: str
    grade: str
    price: float = Field(ge=0)
    delivered: date
    volume: int


# A multi-target transform: one cell → several fields (returns a dict whose
# keys the spec's `targets` maps onto model fields).
def split_location(value, _arg):
    code, _, name = str(value).partition("/")
    return {"code": code.strip(), "name": name.strip()}


fidelis.register_transform("split_location", split_location, overwrite=True)


# A whole-column step: the per-vendor unit is only knowable from the column's
# distribution. The divisor comes from the run context, so the same step serves
# vendors with different units.
@fidelis.register_column_step("normalize_volume", overwrite=True)
def normalize_volume(values, context):
    divisor = (context or {}).get("unit_divisor", 1000)
    nums = [v for v in values if isinstance(v, (int, float))]
    if nums and statistics.median(nums) > 10_000:        # reported in millilitres
        return [v // divisor if isinstance(v, (int, float)) else v for v in values]
    return values


def main() -> None:
    parser = Parser(Quote, spec_store=HERE, llm=None, context={"unit_divisor": 1000})
    result = parser.parse(HERE / "data.csv")
    print(result.summary())
    print("valid:")
    for row in result.valid_rows:
        print("  ", row)
    for err in result.errors:
        print("quarantined:", err.reason)


if __name__ == "__main__":
    main()
