"""01 — basic: rename columns onto a model, with transforms and a default.

    python examples/showcase/01_basic/run.py

Reads data.csv, applies spec.yaml (in this folder), prints validated rows.
No LLM, no network — the spec already exists.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))  # repo root → import fidelis

from fidelis import Parser  # noqa: E402


class User(BaseModel):
    email: str
    full_name: str
    country: str          # defaults to "US" when the source cell is empty


def main() -> None:
    parser = Parser(User, spec_store=HERE, llm=None)
    result = parser.parse(HERE / "data.csv")
    print(result.summary())
    for row in result.valid_rows:
        print(" ", row)


if __name__ == "__main__":
    main()
