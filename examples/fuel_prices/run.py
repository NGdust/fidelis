"""fuel_prices — many vendor CSVs, different shapes, one contract + DB enrichment.

A realistic, fully offline walkthrough of fidelis:

- three vendors, three different CSV layouts (column names, delimiter, units,
  decimal format, ICAO vs IATA codes, missing columns);
- one canonical target model (``FuelPrice``);
- one spec per vendor (already written, in ``specs/``) — so this runs with
  ``llm=None`` and makes ZERO LLM calls;
- constants/defaults via mapping ``value`` (vendor name, currency, code type);
- DB enrichment in **batch** (``batch_enrich``) — one lookup per file, not per row;
- dedup within a file; quarantine for rows that fail; typed rows out.

The "database" is an in-memory dict (see ``hooks.py``); no network, no API key.

    python examples/fuel_prices/run.py
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Make `models` / `hooks` importable and put the repo root on the path so a
# checked-out (un-installed) fidelis is used.
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))

import fidelis  # noqa: E402
import hooks  # noqa: E402,F401  (importing registers transforms + enrichments)
from models import FuelPrice  # noqa: E402


def main() -> None:
    parser = fidelis.Parser(
        FuelPrice,
        spec_store=HERE / "specs",
        llm=None,                       # specs already exist → no LLM needed
        on_unknown_column="error",      # a vendor changing columns is caught, not ignored
    )

    quarantine_dir = HERE / "quarantine"
    quarantine_dir.mkdir(exist_ok=True)

    all_rows: list[FuelPrice] = []
    for csv_path in sorted((HERE / "data").glob("*.csv")):
        result = parser.parse(csv_path)
        print(f"\n=== {csv_path.name} ===")
        print(" ", result.summary())

        if result.errors:
            qpath = quarantine_dir / f"{csv_path.stem}.bad.csv"
            result.write_quarantine(qpath)
            print(f"  {len(result.errors)} bad row(s) -> {qpath.name}")
            for e in result.errors:
                print("   !", e)

        if result.duplicates:
            print(f"  {len(result.duplicates)} duplicate row(s) collapsed")

        for row in result.valid_rows:
            print(
                f"   {row.vendor:<14} {row.airport_code} ({row.code_type})"
                f"  airport_id={row.airport_id}  vendor_id={row.vendor_id}"
                f"  {row.product:<8} {row.price_usd_per_liter} USD/L"
            )
        all_rows.extend(result.valid_rows)

    print(f"\nTotal ingested: {len(all_rows)} prices across all vendors.")
    # In a real job: db.upsert_fuel_prices(all_rows)


if __name__ == "__main__":
    main()
