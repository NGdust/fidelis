"""example_enrichment.py — post-mapping enrichment with your own functions.

A transform sees one cell; an *enrichment* sees the whole mapped record, so it
can derive a field that has no source column. Here the feed carries only an
e-mail and a name, but the target model also wants ``domain`` (from the e-mail)
and ``full_name`` upper-cased — both produced by registered enrichers that run
after mapping and before validation.

Runs fully offline via ``FakeProvider`` (no API key, no network):

    python3 examples/example_enrichment.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from pydantic import BaseModel

HERE = Path(__file__).resolve().parent

# Make the example runnable without installing the package (repo root on path).
sys.path.insert(0, str(HERE.parent))

import fidelis  # noqa: E402
from fidelis import FakeProvider, Parser, register_enrichment  # noqa: E402


class Contact(BaseModel):
    email: str
    full_name: str
    domain: str  # no source column — filled by enrichment


CONTACT_MAPPINGS = [
    {"source": "E-mail", "target": "email", "transform": "strip_lower", "confidence": 0.98},
    {"source": "Name", "target": "full_name", "transform": "strip", "confidence": 0.91},
]


@register_enrichment("fill_domain")
def fill_domain(record, source):
    """Derive the e-mail domain from the already-mapped ``email`` field."""
    record["domain"] = record["email"].split("@", 1)[1]
    return record


@register_enrichment("shout_name")
def shout_name(record, source):
    """Combine/transform across the record — here, upper-case the full name."""
    record["full_name"] = record["full_name"].upper()
    return record


RECORDS = [
    {"E-mail": " Alice@B.COM ", "Name": " Alice Anderson "},
    {"E-mail": "bob@x.io", "Name": "Bob Brown"},
]


def main() -> None:
    provider = FakeProvider(mappings=CONTACT_MAPPINGS)
    print("Registered enrichments:", fidelis.available_enrichments())

    with tempfile.TemporaryDirectory() as tmp:
        parser = Parser(
            target_model=Contact,
            spec_store=tmp,
            llm=provider,
            enrich=["fill_domain", "shout_name"],  # by name, applied in order
        )
        result = parser.parse(RECORDS)
        print(result.summary())
        for contact in result.valid_rows:
            print("  ", contact)


if __name__ == "__main__":
    main()
