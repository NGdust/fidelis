"""02 — enrichment: combine raw columns + one bulk lookup.

    python examples/showcase/02_enrichment/run.py

- `full_name` is built from the raw First/Last columns (an enricher sees the
  source row);
- `score` is attached for the whole feed in one pass (a batch enricher).
The spec references both by the names registered here.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))

import fidelis  # noqa: E402
from fidelis import Parser  # noqa: E402


class Contact(BaseModel):
    email: str
    full_name: str
    score: int


# A stand-in "scores service" — replace with your DB / API.
_SCORES = {"u1": 10, "u2": 20}


@fidelis.register_enrichment("full_name", overwrite=True)
def full_name(record, source):
    record["full_name"] = f"{source['First']} {source['Last']}".strip()
    return record


@fidelis.register_batch_enrichment("attach_scores", overwrite=True)
def attach_scores(records):
    for r in records:                      # ONE pass for the whole file
        r["score"] = _SCORES.get(r["user_id"], 0)
    return records


def main() -> None:
    result = Parser(Contact, spec_store=HERE, llm=None).parse(HERE / "data.csv")
    print(result.summary())
    for row in result.valid_rows:
        print(" ", row)


if __name__ == "__main__":
    main()
