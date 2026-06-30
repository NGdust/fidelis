"""Custom transforms + DB enrichments for the fuel-prices example.

Importing this module registers everything the specs reference by name. The
"database" here is a plain in-memory dict so the example runs offline; in a real
project these functions would hit your actual DB / FX service. The point is the
shape: enrichment from the DB is done in **batch** — one lookup per file, not
one per row.
"""

from __future__ import annotations

import fidelis

# --------------------------------------------------------------------------- #
# A stand-in for "our database" (replace with real queries)
# --------------------------------------------------------------------------- #

_AIRPORTS = {
    ("KJFK", "icao"): 100,
    ("KLAX", "icao"): 101,
    ("EGLL", "icao"): 200,
    ("LFPG", "icao"): 201,
    ("DXB", "iata"): 300,
    ("SIN", "iata"): 301,
}
_VENDORS = {"Shell Aviation": 1, "BP Aviation": 2, "WFS": 3}
_FX_TO_USD = {"USD": 1.0, "EUR": 1.08}
_USG_TO_LITER = 3.78541


class _FakeDB:
    """Minimal bulk-query API mirroring what a real client would expose."""

    @staticmethod
    def airport_ids(pairs):
        # ONE query: SELECT id, code, code_type FROM airports WHERE (code,code_type) IN (...)
        return {p: _AIRPORTS.get(p) for p in pairs}

    @staticmethod
    def vendor_ids(names):
        # ONE query: SELECT id, name FROM vendors WHERE name IN (...)
        return {n: _VENDORS.get(n) for n in names}

    @staticmethod
    def fx_rates_to_usd(day):
        # ONE query: SELECT currency, rate FROM fx WHERE day = ...
        return dict(_FX_TO_USD)


db = _FakeDB()


# --------------------------------------------------------------------------- #
# Per-cell transform (European number format)
# --------------------------------------------------------------------------- #


def eu_float(value, arg):
    """'1 234,56' -> 1234.56 (spaces as thousands, comma as decimal)."""
    text = str(value).replace(" ", "").replace(" ", "").replace(",", ".")
    return float(text)


# register_transform is a plain call (not a decorator like the enrichments).
fidelis.register_transform("eu_float", eu_float, overwrite=True)


# Row expansion (one row that lists several airports → one record per airport) is
# fully declarative in specs/spec_multi_eu.yaml — `field: airport_code` +
# `delimiter: "|"` — so there's no expander to register here.


# --------------------------------------------------------------------------- #
# Batch enrichments — one bulk lookup for the whole file
# --------------------------------------------------------------------------- #


@fidelis.register_batch_enrichment("resolve_airports", overwrite=True)
def resolve_airports(records):
    pairs = {(r["airport_code"], r["code_type"]) for r in records}
    index = db.airport_ids(pairs)                       # one query
    for r in records:
        r["airport_id"] = index.get((r["airport_code"], r["code_type"]))
    return records


@fidelis.register_batch_enrichment("resolve_vendors", overwrite=True)
def resolve_vendors(records):
    index = db.vendor_ids({r["vendor"] for r in records})   # one query
    for r in records:
        r["vendor_id"] = index.get(r["vendor"])
    return records


@fidelis.register_batch_enrichment("price_to_usd_per_liter", overwrite=True)
def price_to_usd_per_liter(records):
    fx = db.fx_rates_to_usd(records[0]["valid_from"])       # one query for the day
    for r in records:
        usd = float(r["price"]) * fx[r["currency"]]
        per_liter = usd / _USG_TO_LITER if r["unit_volume"] == "usg" else usd
        r["price_usd_per_liter"] = round(per_liter, 4)
    return records
