# Example: airport fuel prices from many vendors

A realistic, **fully offline** walkthrough: many vendor CSVs with different
shapes, coerced into one model, then enriched from "our database" — the exact
shape of a partner-feed ingestion job.

```bash
python examples/fuel_prices/run.py
```

No API key, no network, no real DB (the database is an in-memory dict in
`hooks.py`). It makes **zero LLM calls** because a spec already exists for each
vendor.

## The problem

Three vendors, three different CSV layouts:

| File | Delimiter | Codes | Price unit | Quirks |
| ---- | --------- | ----- | ---------- | ------ |
| `vendor_shell_us.csv` | `,` | ICAO | USD / **gallon** | no `vendor` / `currency` columns |
| `vendor_bp_eu.csv`    | `;` | ICAO | **EUR** / liter | comma decimals (`0,71`), `dd.mm.yyyy` |
| `vendor_wfs_intl.csv` | `,` | **IATA** | USD / liter | no `product` column |
| `vendor_multi_eu.csv` | `;` | ICAO | EUR / liter | **several airports in one cell** (`EGLL\|LFPG`) |

All must become one canonical [`FuelPrice`](models.py), with `airport_id`,
`vendor_id`, and a normalized `price_usd_per_liter` looked up from our DB.

## How the pieces map

| Need | How fidelis does it | Where |
| ---- | ------------------- | ----- |
| Different column names → one model | per-vendor **mapping** | `specs/*.yaml` |
| Missing `vendor` / `currency` | `value:` **constant** | spec mappings |
| Missing `product` | model **default** (`"Jet A-1"`) | `models.py` |
| ICAO vs IATA | `code_type` stamped via `value:` | spec mappings |
| `0,71` → `0.71` | custom `eu_float` **transform** | `hooks.py` |
| `airport_id` / `vendor_id` from DB | **batch enrichment** — one query per file | `hooks.py` + `batch_enrich:` |
| EUR & gallons → USD/liter | batch enrichment with FX | `hooks.py` |
| Several airports in one row | **row expansion** — declarative `expand: [{field, delimiter}]` → one record per airport, each enriched on its own | `specs/spec_multi_eu.yaml` |
| Duplicate (airport, product) rows | `dedup:` keep last | spec |
| A row with a bad price | **quarantine** (not silently dropped) | `run.py` |
| A vendor renames a column | **drift** detection | `on_unknown_column: error` |

The key point for DB enrichment: it runs in **batch** (`batch_enrich`), so each
file triggers *three* DB lookups total, not three-per-row.

## Files

```
fuel_prices/
  models.py            # the canonical FuelPrice model
  hooks.py             # custom transform + DB batch enrichments (+ a fake DB)
  specs/               # one reviewed spec per vendor (the contract)
  data/                # the sample CSVs (one per vendor)
  run.py               # the ingestion loop
```

## CLI (CI gates)

Because the specs reference custom transforms/enrichments registered in
`hooks.py`, pass `--import hooks` so the CLI loads them (run from this folder):

```bash
# lint every spec against the model + registered hooks
fidelis validate-spec specs/*.yaml --model models:FuelPrice --import hooks

# fail the build if a vendor's columns drifted
fidelis check-drift data/vendor_bp_eu.csv --model models:FuelPrice --spec-dir specs

# onboard a brand-new vendor: draft a spec for review (needs --llm)
fidelis generate-spec data/new_vendor.csv --model models:FuelPrice \
    --llm anthropic:claude-opus-4-8 --import hooks --show
```
