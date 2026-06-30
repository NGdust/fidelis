<div align="center">

# fidelis

**Every partner sends the same data in a different shape. fidelis maps any of
them onto one Pydantic model — an LLM writes the mapping once, then never runs
again.**

[![Python](https://img.shields.io/badge/python-3.11%2B-3776ab?logo=python&logoColor=white)](https://www.python.org/)
[![Pydantic](https://img.shields.io/badge/pydantic-v2-e92063?logo=pydantic&logoColor=white)](https://docs.pydantic.dev/)
[![License](https://img.shields.io/badge/license-MIT-22c55e)](#license)
[![Tests](https://img.shields.io/badge/tests-passing-brightgreen)](#)

</div>

---

A CSV from one partner, an Excel sheet from another, a JSON webhook from a
third: the same data, with different column names, units, and quirks — and a
field that's missing here and renamed there. Instead of a hand-written parser per
source, you point fidelis at each one. The first time it meets a new shape, an
LLM writes a versioned mapping **spec** — human-readable YAML you review and
commit. Every run after that is pure, deterministic Python: **zero LLM calls**,
every row validated, schema drift caught, and nothing dropped silently.

> A file is just one adapter. Identity is bound to what the
> fields *mean* (their signature), not to a filename — so one spec covers a CSV
> today and the same vendor's JSON tomorrow.

### Why fidelis

| | |
| --- | --- |
| 🧠 **LLM once, then never** | An LLM writes the spec the first time it sees a source shape. Every run after is pure, deterministic Python — no network, no surprises. |
| 🔌 **Any source, one `parse()`** | CSV, Excel, JSON, or a `list[dict]` payload — matched by what fields *mean*, not by filename. One call, same result. |
| 📝 **The spec is the contract** | A versioned, reviewable YAML — mapping, transforms, enrichment, and dedup all in one place. You diff it in PRs, not reverse-engineer a black box. |
| 🛟 **Never lose a row** | Every input row becomes a validated record *or* a `RowError` with its row, field, and reason — then quarantine, fix, and re-ingest. |
| 🔎 **Drift-aware** | A vendor adds or renames a column and fidelis catches it under an explicit policy — caught in CI, not silent corruption in prod. |
| 🪶 **Light & provider-agnostic** | `pydantic` + `pyyaml` at the core; Anthropic / OpenAI / local LLMs are optional extras. |

### Contents

- [Install](#install)
- [The headline: one `parse()`, any source, same result](#the-headline-one-parse-any-source-same-result)
- [Worked examples (`examples/showcase/`)](examples/showcase/)
- [How spec generation & caching work](#how-spec-generation--caching-work--llm-once-then-deterministic)
- [The YAML spec format](#the-yaml-spec-format)
- [Schema drift handling](#schema-drift-handling-on_unknown_column)
- [Quarantine round-trip](#quarantine-round-trip)
- [Transforms](#transforms)
- [Enrichment (post-mapping hooks)](#enrichment-post-mapping-hooks)
- [Shaping output (multi-record, multi-field, whole-column)](#shaping-output-multi-record-multi-field-whole-column)
- [Run context, coverage & LLM hints](#run-context-coverage--llm-hints)
- [Provider strings](#provider-strings-anthropic--openai--local)
- [Testing offline with `FakeProvider`](#testing-offline-with-fakeprovider)
- [API surface](#api-surface)
- [License](#license)

---

## Install

```bash
pip install fidelis            # core (pydantic + pyyaml)
# or pull in what you need:
pip install 'fidelis[all]'     # + Excel, HTTP providers, pandas & polars
pip install 'fidelis[excel]'   # + openpyxl for .xlsx sources
pip install 'fidelis[anthropic]' # + httpx for the Anthropic provider
```

Requires Python ≥ 3.11.

> Working from a clone? Install in editable mode instead: `pip install -e '.[dev]'`.

---

## Don't have a model yet?

`infer_model_source` writes a draft Pydantic class from a sample — field names
and types inferred from the data (deterministic, no LLM). Review it, then use it
as your target:

```python
from fidelis import infer_model_source
print(infer_model_source("feed.csv", class_name="User"))
```

Inference: textual booleans → `bool`, whole numbers → `int`, decimals →
`float`, ISO/`dd.mm.yyyy` dates → `date`, else `str`; any empty/missing value in
the sample makes the field `Optional[...] = None`.

---

## The headline: one `parse()`, any source, same result

The *same* `parser.parse()` consumes a CSV **file** and a `list[dict]`
**payload**. Because both carry the same fields by meaning, they resolve to the
**same cached spec** and produce **identical** `valid_rows`.

```python
from datetime import date
from pydantic import BaseModel
from fidelis import Parser

class User(BaseModel):
    email: str
    full_name: str
    signup_date: date

parser = Parser(
    target_model=User,
    spec_store="specs/",                # where specs live (a path, or a custom SpecStore)
    llm="anthropic:claude-opus-4-8",    # only used the first time a shape is seen
    on_unknown_column="error",          # default drift policy
)

# 1) A CSV file on disk → spec generated once and written to specs/.
from_file = parser.parse("incoming/acme_users.csv")

# 2) A list[dict] payload with the SAME fields → SAME cached spec, 0 LLM calls.
from_records = parser.parse([
    {"E-mail": " a@b.com ", "Name": " Alice ", "Date": "01.02.2026"},
    {"E-mail": "bob@x.com", "Name": "Bob",     "Date": "15.03.2026"},
])

assert from_file.valid_rows == from_records.valid_rows   # identical Users

# Inspect the result of a parse:
from_file.valid_rows      # list[User]   — validated records
from_file.errors          # list[RowError] — what didn't fit, and why (never silent)
from_file.spec_used       # the Spec that was applied
from_file.spec_generated  # was the spec generated by the LLM in THIS run?
from_file.drift_report    # schema drift, if any
from_file.needs_review    # bool — any mappings still flagged needs_review?
print(from_file.summary())
# valid=2 errors=0 coverage=1.00 needs_review=True drift=False generated=True
```

> Runnable, offline versions live in [`examples/`](examples/) — no API key, no
> network. For a guided tour, [`examples/showcase/`](examples/showcase/) walks one
> feature per folder (a CSV, a `run.py`, and the spec), simplest first:
> ```bash
> python examples/showcase/01_basic/run.py        # rename + transforms + default
> python examples/showcase/03_expand/run.py        # one row → many records
> python examples/showcase/06_advanced/run.py      # the whole pipeline at once
> ```

### Other sources & the `kind` hint

`parse()` infers the source kind from the type / file extension. Pass `kind=`
to be explicit (e.g. a JSON string, or CSV text with no `.csv` extension):

```python
parser.parse("incoming/users.xlsx")                  # Excel
parser.parse(json_payload, kind="json")              # JSON array
parser.parse("E-mail;Name;Date\n...", kind="csv")    # raw CSV text
```

**Remote & compressed sources** are handled transparently — pass an HTTP(S) URL
or a `.gz` path and fidelis fetches/decompresses before dispatch:

```python
parser.parse("https://example.com/daily/feed.csv")   # fetched over HTTP(S)
parser.parse("exports/users.json.gz")                # gunzipped, then parsed
parser.parse("https://example.com/feed.csv.gz")      # both
```

The adapter kind is inferred from the URL path / inner extension. (Excel is a
binary container read from a path, so Excel-over-URL/gzip is not supported —
download it first.)

### Dedup / upsert keys

Declare the row key **in the spec** and fidelis collapses duplicate rows within
a feed, keeping the `first` (default) or `last` occurrence. Dropped rows are
never lost silently — they come back as `result.duplicates`, each pairing the
kept row with the one it displaced.

```yaml
# in the spec:
dedup:
  key: [email]        # one or more model fields; composite key supported
  keep: first         # first | last (last = upsert-within-feed)
```

```python
result = parser.parse(feed)
print(len(result.valid_rows), "kept,", len(result.duplicates), "dropped")
for d in result.duplicates:
    print(d.key, "→ dropped", d.dropped)
```

The key is matched **after** validation/coercion, so `"A@B.com"` and `"a@b.com"`
collide once `strip_lower` has run. `validate_spec` flags a key field that isn't
on the model.

**Global default.** `Parser(User, dedup_key="email", dedup_keep="last")` still
works for specs that don't declare their own `dedup` (pass a list or
comma-separated string for a composite key). A spec's `dedup` takes precedence
over the Parser default.

### Typed output

`ParseResult` hands the validated rows straight into your stack:

```python
result.to_dicts()              # list[dict] (mode="json" for JSON-native scalars)
result.to_pandas()             # pandas.DataFrame   — pip install 'fidelis[pandas]'
result.to_polars()             # polars.DataFrame   — pip install 'fidelis[polars]'
result.errors_to_dicts()       # rejected rows for a dead-letter file / report
```

---

## How spec generation & caching work — LLM once, then deterministic

Identity is bound to the **field signature**, not to a filename. The signature
is a short hash of the *normalized* set of source field names (trim, lower,
collapsed whitespace/underscores). Field order and case do not matter, so one
spec covers a CSV today and a JSON payload tomorrow if they mean the same thing.

```
data → fingerprint of field names
  ├─ spec found      → deterministic mapping (0 LLM calls)
  └─ no spec         → LLM generates a draft → saved to the spec store
                     → you review it (especially needs_review mappings)
                     → every subsequent run is LLM-free
```

What goes to the LLM is intentionally tiny and one-shot: only the **inventory of
field names**, a **small sample** of rows (≤20 by default), and the **JSON
schema of your target model** — never the whole dataset, and never per-row.
Low-confidence mappings (below `confidence_threshold`, default `0.8`) are
auto-flagged `needs_review` so you know exactly what to check by hand.

Helper methods for review flows / CI:

```python
spec = parser.generate_spec(source)      # generate a draft Spec, don't parse
problems = parser.validate_spec(spec)    # [] means the spec is well-formed
```

`validate_spec` checks that every `target` (and `targets`) exists on the model,
isn't mapped twice, every required model field is covered, transforms are known,
confidences are in `[0, 1]`, and that everything the spec references by name —
`enrich` / `batch_enrich` / `expand` expanders / `column_steps` / `dedup` keys,
plus transforms inside `rules` — is registered and valid.

### Where specs live (`spec_store`)

By default specs are YAML files next to your project (`spec_store="specs/"`).
In production you may want them in **S3, a database, or a config service** —
implement `SpecStore` and pass it in. The same `spec_store` argument takes either
a path or a store:

```python
Parser(User, spec_store="specs/")          # files (default)
Parser(User, spec_store=S3SpecStore(...))  # your backend
```

A spec's identity is its **field signature** (the hash of the source field
names), which maps cleanly onto a storage key — `get(signature)` is one object
GET / one `SELECT` by primary key, no scanning. Only drift detection looks across
specs (via `all()`); a backend where listing everything is impractical can
override `find_drift_candidate` or skip it.

```python
from fidelis import SpecStore, Spec

class S3SpecStore(SpecStore):
    def __init__(self, bucket, prefix=""):
        self.s3, self.bucket, self.prefix = boto3.client("s3"), bucket, prefix

    def get(self, signature):                       # one GET, keyed by signature
        try:
            obj = self.s3.get_object(Bucket=self.bucket,
                                     Key=f"{self.prefix}spec_{signature}.yaml")
        except self.s3.exceptions.NoSuchKey:
            return None
        return Spec.from_yaml(obj["Body"].read().decode())

    def save(self, spec):
        self.s3.put_object(Bucket=self.bucket,
                           Key=f"{self.prefix}spec_{spec.signature}.yaml",
                           Body=spec.dump_yaml().encode())

    # implement all() too if you want schema-drift detection
```

`FileSpecStore` and an in-memory `MemorySpecStore` (handy for tests) ship built-in.

---

## The YAML spec format

A spec is a human-readable, version-controlled contract for ingesting **one**
source format. A real, hand-written example lives at
[`specs/partner_acme_users.yaml`](specs/partner_acme_users.yaml) and maps onto
the `User` model above:

```yaml
version: 1
generated_by: claude-opus-4-8        # or "human" after a manual edit
generated_at: "2026-06-28"
signature: 6a93a9                     # hash of the normalized source field names

# Stage 1 — structural dialect. Only for text/file sources (CSV/TSV);
# skipped entirely for already-structured inputs (list[dict] / JSON).
parsing:
  delimiter: ";"
  encoding: utf-8
  quote_char: '"'

# Stage 2 — universal field mapping (source column -> target model field).
mappings:
  - target: email
    source: "E-mail Address"
    transform: strip_lower
    confidence: 0.98
    status: ok

  - target: full_name
    source: "Customer Full Name"
    transform: strip
    confidence: 0.91
    status: ok

  - target: signup_date
    source: "Registration Date"
    transform: "parse_date:%d.%m.%Y"
    confidence: 0.62
    status: needs_review     # LLM unsure — a human verifies, then sets ok

  - target: source_system    # a CONSTANT — no source column
    value: "acme"

  - target: country          # source if present, else this DEFAULT
    source: "Country"
    value: "US"

# Stage 3 — record-level steps, referenced by registered name (you add these by
# hand; the LLM never writes them). See "Enrichment", "Row expansion", "Dedup",
# and "Shaping output" for rules / unpivot / column_steps / skip_when.
expand:                      # one row -> many records
  - field: airport_code      # split this column...
    delimiter: "|"           # ...on this separator (declarative, no code)
enrich:                      # per-row (runs on each fanned-out row)
  - fill_domain
batch_enrich:               # whole-batch, register_batch_enrichment
  - attach_scores
dedup:                      # collapse duplicate rows after validation
  key: [email]
  keep: first               # first | last

# Per-source drift policy (overrides the Parser's global setting).
on_unknown_column: error     # ignore | error | regenerate
```

Field notes:

- **`signature`** is the primary key binding the spec to data; it is what
  `parse()` looks up. Editing source field names changes it. The target model is
  **not** in the spec — you bind it in code (`Parser(target_model=...)`), so a
  spec maps source fields onto field *names* and stays model-agnostic.
- **`status`** is `ok` or `needs_review`; a `needs_review` mapping is surfaced
  via `ParseResult.needs_review`.
- **`transform`** is a built-in name or `name:arg` (see below).
- **`value`** sets a target without (or as a fallback to) a source: with no
  `source` it's a constant; with a `source` it's the default used when the source
  cell is empty/missing. A mapping needs at least one of `source` / `value`.
- **`expand` / `enrich` / `batch_enrich` / `dedup`** describe the record-level
  steps **in the spec**, by registered name — so the YAML is the *whole* contract
  and varies per source. The functions are registered in code; the spec just
  references them, exactly like `transform`. (The matching `Parser(...)` arguments
  still work as a global default for specs that don't declare a step.)
- **`parsing`** is only meaningful for text sources and is ignored for
  structured inputs.

---

## Schema drift handling (`on_unknown_column`)

When a familiar source gains or loses a column, its field signature changes and
no spec matches exactly. `fidelis` recognizes this as **drift** of a known
source (by field-set similarity) rather than a brand-new format, and applies the
policy from the spec's `on_unknown_column` (falling back to the `Parser`
default). In every case `ParseResult.drift_report` describes exactly what
changed.

| Policy        | Behavior                                                              |
| ------------- | -------------------------------------------------------------------- |
| `ignore`      | Silently ignore unknown columns and keep mapping the known ones.     |
| `error`       | **(default)** Raise `DriftError` with a precise message; lose nothing silently. |
| `regenerate`  | Ask the LLM to fill in *only* the missing part of the mapping, then continue. |

```python
parser = Parser(target_model=User, llm="anthropic:claude-opus-4-8",
                on_unknown_column="regenerate")
result = parser.parse(source_with_a_new_column)
print(result.drift_report.describe())
# Schema drift (regenerate): new fields: Phone
```

> **Note on detection limits.** Since specs are keyed by a hash of the source
> field names, a drifted source is recognized as "the same source, changed" only
> when its field set still overlaps a known spec's by **≥ 50%** (Jaccard). If a
> source mutates so heavily that most columns are renamed at once, it is
> indistinguishable from a brand-new format and a fresh spec is generated instead
> of raising drift. Heavy renames therefore need a fresh review of the new spec.

> **Note on the `error` policy.** Because `error` *raises* `DriftError`, no
> `ParseResult` is returned — the drift details live on `exc.report` instead of
> `result.drift_report`. Use `ignore` or `regenerate` if you need the drift
> reported inside the result object.

---

## Quarantine round-trip

No bad row is ever dropped silently — every failure is a `RowError` carrying the
**original** source record. The quarantine helpers turn those into a file a
human can fix, then read it back into clean records to re-ingest:

```python
result = parser.parse("feed.csv")
result.write_quarantine("bad_rows.csv")      # original data + _error_reason column
# …a human opens bad_rows.csv and fixes the offending cells…

from fidelis import read_quarantine
fixed = read_quarantine("bad_rows.csv")      # diagnostic columns stripped
again = parser.parse(fixed)                  # same field signature → same spec
```

Each exported row is the source record plus three `_`-prefixed diagnostic
columns (`_row_index`, `_error_field`, `_error_reason`) that are stripped on the
way back in — so the re-ingested rows keep the original field signature and reuse
the same spec (no spurious drift). `.json` paths round-trip as JSON, anything
else as CSV.

---

## Transforms

A transform normalizes a raw cell before validation. Reference it in the spec by
name, or `name:arg`. Built-ins:

| Transform     | Example                | Effect                                             |
| ------------- | ---------------------- | -------------------------------------------------- |
| `strip`       | `strip`                | Trim surrounding whitespace.                       |
| `strip_lower` | `strip_lower`          | Trim and lowercase (great for emails).             |
| `to_int`      | `to_int`               | Parse to `int` (tolerant of `"12.0"`, spaces).     |
| `to_float`    | `to_float`             | Parse to `float` (accepts `,` decimal separator).  |
| `to_bool`     | `to_bool`              | `yes/1/true/y → True`, `no/0/false/n → False`.     |
| `parse_date`  | `parse_date:%d.%m.%Y`  | `strptime` with the given format (ISO if omitted). Pipe-separate several formats to try in order: `parse_date:%Y-%m-%d\|%d/%m/%Y`. |
| `clip`        | `clip:0:100`           | Clamp a number into a range (`clip:0:` / `clip::100` for one-sided).  |

Empty values pass through untouched — requiredness is enforced by Pydantic
validation, not by transforms. Register your own from code:

```python
from fidelis import register_transform, available_transforms

# Plain call…
register_transform("upper", lambda value, arg: str(value).upper())

# …or as a decorator (like register_enrichment / register_expander):
@register_transform("shout")
def shout(value, arg):
    return str(value).upper() + "!"

print(available_transforms())   # [..., 'shout', 'to_int', 'upper', ...]
```

Then use `transform: upper` in any spec.

---

## Enrichment (post-mapping hooks)

A transform sees a single cell. An **enrichment** sees the **whole mapped
record** *and* the **original source row** — so it can derive a field with no
source column, combine several columns (mapped or raw), mask a value, or look
something up in an external source. Enrichers run after mapping and before
validation, so any field they add is validated like the rest.

Like a transform, you **register the function in code and reference it by name
in the spec** — so the YAML stays the full contract and enrichment can vary per
source. The signature is `(record, source)`: `record` is the mapped target dict,
`source` is the raw input row.

```python
import fidelis

@fidelis.register_enrichment("fill_domain")
def fill_domain(record, source):
    record["domain"] = record["email"].split("@", 1)[1]
    return record            # or mutate in place and return None

# Reach RAW columns that aren't mapped to any target — e.g. combine two:
@fidelis.register_enrichment("full_name_from_parts")
def full_name_from_parts(record, source):
    record["full_name"] = f"{source['First Name']} {source['Last Name']}".strip()
    return record
```

```yaml
# in the spec for this source:
enrich:
  - fill_domain
  - full_name_from_parts
```

- Enrichers are applied in spec order — each sees the previous one's output.
- `validate_spec` flags an `enrich` name that isn't registered; at parse time an
  unknown name raises loudly.
- If an enricher raises, that record becomes a `RowError` (never silently
  dropped), with the failing enricher's name in the reason.
- `available_enrichments()` lists what's registered.

**Global default.** `Parser(User, enrich=["fill_domain"])` still works — it
applies to every spec that doesn't declare its own `enrich`. The constructor arg
also accepts callables directly (handy for quick scripts); specs reference names
only. Spec-declared steps take precedence over the Parser default.

How it differs from a transform:

|              | Transform                     | Enrichment                              |
| ------------ | ----------------------------- | --------------------------------------- |
| Sees         | one cell value                | the mapped record **+ the source row**  |
| Configured   | `transform:` in the spec      | `enrich:` in the spec                   |
| Can add fields with no source column | no            | yes                                     |
| Runs when    | during mapping                | after mapping, before validation        |

> Combine (many → one), split (one → many), coalesce, and conditional mapping
> are all just enrichers that read `source` — declared in the spec, registered
> in code. Plain 1→1 column mapping and constants stay in `mappings`.

### Batch enrichment — one bulk lookup instead of N

A per-row enricher that calls a database or an API runs once **per row**. When
the lookup can be batched, register a **batch enricher** instead: it receives
every clean record at once and returns a list of the same length, in order — so
you make a single bulk call for the whole feed.

```python
@fidelis.register_batch_enrichment("attach_scores")
def attach_scores(records):
    ids = [r["user_id"] for r in records]
    scores = score_service.bulk_fetch(ids)      # ONE call, not len(records)
    for r, s in zip(records, scores):
        r["score"] = s
    return records                               # same length, same order
```

```yaml
# in the spec:
batch_enrich:
  - attach_scores
```

- Batch enrichers run **after** the per-row `enrich`, over the clean rows
  only — rows that failed mapping are excluded and preserved as errors.
- The one-to-one contract is enforced: returning a different length (or a
  non-dict) raises `BatchEnrichmentError`. Batch enrichment is all-or-nothing —
  use it to fill/derive fields across the feed, not to filter rows.
- Configuring `batch_enrich` materializes the feed (it needs the whole set);
  without it the pipeline stays lazy.
- As with `enrich`, `Parser(batch_enrich=[...])` is a global default for specs
  that don't declare their own.

### Row expansion (one row → many)

Sometimes a single source row stands for several entities — one line whose
`Airports` column lists `"JFK;LAX;ORD"` should become **three** rows. An
**expander** returns a *list* of records; the row fans out, and then `enrich` /
`batch_enrich` / validation / `dedup` all run **per fanned-out row** — so each
airport gets its own lookup and its own derived fields.

The common case — split one column — is **fully declarative, no code**: name the
column and the delimiter right in the spec.

```yaml
expand:
  - field: airport_code      # the (target) column holding the list
    delimiter: "|"           # the list separator — each vendor sets its own
enrich:
  - resolve_airport          # runs once per airport, not per source line
```

For arbitrary fan-out, register a custom expander — `(record, source) → list` —
and reference it with `expander:`:

```python
@fidelis.register_expander("by_region")
def by_region(record, source):
    return [{**record, "region": r} for r in lookup_regions(source["Airports"])]
```

```yaml
expand:
  - expander: by_region
```

Expanders run **before** `enrich`, so enrichment is per fanned-out row (per
airport).

- Pipeline order: **map → expand → enrich → batch_enrich → validate → dedup.**
- A step may return zero, one, or many rows. If one fanned-out row fails, it
  becomes a `RowError` pointing back at the source line; its siblings still pass.
- `Parser(expand=[...])` is a global default (registered names or callables) for
  specs that don't declare their own.

---

## Shaping output (multi-record, multi-field, whole-column)

Beyond 1 row → 1 record, the spec can reshape the output. Full pipeline:
**skip_when → unpivot → rules → map → column_steps → expand → enrich →
batch_enrich → validate → dedup**.

**Conditional multi-record (`rules`)** — one row → a record per firing rule
(base `mappings` + the rule's). E.g. a row with retail *and* wholesale prices
becomes two records:

```yaml
mappings:
  - {target: sku, source: SKU, transform: strip}     # shared
rules:
  - when: {field: RETAIL_PRICE, op: not_empty}        # ops: not_empty/empty/eq/ne/in/gt/lt/ge/le
    mappings:
      - {target: kind, value: retail}
      - {target: amount, source: RETAIL_PRICE, transform: to_float}
  - when: {field: WHOLESALE_PRICE, op: not_empty}
    mappings:
      - {target: kind, value: wholesale}
      - {target: amount, source: WHOLESALE_PRICE, transform: to_float}
```

**Column-group fan-out (`unpivot`)** — repeating groups → one record each:

```yaml
unpivot:
  count: 3                       # indices 1..3 (or list them in `index`)
  index_field: quarter
  columns: {PRICE: "Q{i}_PRICE", QTY: "Q{i}_QTY"}
```

**Multi-target transform (`targets`)** — one transform fills several fields
(keep the converted *and* the original). The transform returns a dict:

```yaml
mappings:
  - {source: WEIGHT, transform: lbs_to_kg,
     targets: {kg: weight_kg, original: weight_original, unit: weight_unit}}
```

**Whole-column stage (`column_steps`)** — decisions over a column's distribution,
applied before validation (median→/100, 2→4-digit year):

```python
@fidelis.register_column_step("cents_if_huge")
def cents_if_huge(values, context=None):       # whole column as a list, same length out
    import statistics
    nums = [v for v in values if isinstance(v, (int, float))]
    if nums and statistics.median(nums) > 10_000:
        return [v / 100 if isinstance(v, (int, float)) else v for v in values]
    return values
```
```yaml
column_steps: {price: cents_if_huge}
```

**Skip & clip** — drop rows declaratively, clamp numbers:

```yaml
skip_when:
  - {field: STATUS, op: in, value: [CANCELLED, VOID]}   # drop these rows
mappings:
  - {target: pct, source: PCT, transform: "clip:0:100"}  # clamp into [0, 100]
```

Skipped / no-rule-fired rows produce no record but **still count** in the
coverage denominator (below).

---

## Run context, coverage & LLM hints

**Run context (`context=`)** — hand lookups / thresholds / config to hooks
instead of hardcoding globals. Any transform / enricher / batch / expander /
column-step that declares a `context` parameter receives it (others are
unaffected); it's isolated per `parse()`:

```python
@fidelis.register_transform("canon")
def canon(value, arg, context):
    return context["synonyms"].get(str(value).strip(), value)

Parser(Supplier, context={"synonyms": {...}, "threshold": 0.85})
```

**Coverage (`result.coverage`)** — one quality number for the whole run: the
fraction of source rows that produced a record.

```python
result.coverage          # Coverage(rows_in=1000, rows_with_output=980, rows_with_error=12, …)
result.coverage.score    # 0.98  — also in result.summary()
```

**Domain hints (`domain_hints=`)** — give the LLM typed context when it generates
a spec (only affects generation, never the deterministic path):

```python
Parser(Item, llm="anthropic:claude-opus-4-8",
       domain_hints={"currencies": ["USD", "EUR"], "categories": ["A", "B", "C"]})
```

---

## Provider strings (`anthropic` / `openai` / `local`)

Pick a provider with a `"provider:model"` string (or pass a provider instance).
The provider is only ever used for one-shot schema inference — never per row.

```python
Parser(target_model=User, llm="anthropic:claude-opus-4-8")     # Anthropic Messages API
Parser(target_model=User, llm="openai:gpt-4o-mini")            # OpenAI
Parser(target_model=User, llm="local:llama3")                  # any OpenAI-compatible endpoint
```

- **`anthropic`** — reads `ANTHROPIC_API_KEY` from the environment (or pass
  `llm_options={"api_key": ...}`). Needs `httpx` (`pip install 'fidelis[anthropic]'`).
- **`openai`** — reads `OPENAI_API_KEY`. Needs `httpx`.
- **`local`** — any OpenAI-compatible server (Ollama, vLLM, LM Studio…);
  defaults to `http://localhost:11434/v1/chat/completions`, no API key required.
  Override with `llm_options={"base_url": ...}` or `LOCAL_LLM_BASE_URL`.

Extra constructor arguments go through `llm_options`:

```python
Parser(target_model=User, llm="anthropic:claude-opus-4-8",
       llm_options={"api_key": "sk-...", "timeout": 30})
```

If no spec matches and no `llm` is configured, `parse()` raises
`SpecNotFoundError` — it will never reach the network behind your back.

---

## Testing offline with `FakeProvider`

`FakeProvider` is a deterministic, network-free `LLMProvider` for tests and
offline flows. Hand it the mappings (or a canned JSON response, or a
`responder` callable) you want it to "infer":

```python
from fidelis import Parser, FakeProvider

provider = FakeProvider(mappings=[
    {"source": "E-mail", "target": "email", "transform": "strip_lower", "confidence": 0.98},
    {"source": "Name",   "target": "full_name", "transform": "strip", "confidence": 0.91},
    {"source": "Date",   "target": "signup_date", "transform": "parse_date:%d.%m.%Y", "confidence": 0.62},
])

parser = Parser(target_model=User, spec_store=tmp_dir, llm=provider)
result = parser.parse([{"E-mail": "a@b.com", "Name": "Alice", "Date": "01.02.2026"}])
assert provider.call_count == 1          # generated once
parser.parse(...)                        # subsequent calls: still 1 — cached, deterministic
```

This is exactly how the examples in [`examples/`](examples/) run with no API key.

---

## API surface

```python
from fidelis import (
    Parser, ParseResult, RowError, DriftReport, DuplicateRow, Coverage, SpecNotFoundError,
    Spec, Mapping, ParsingSpec, DedupSpec, UnpivotSpec, Condition, Rule, ExpandStep,
    compute_signature, find_spec_by_signature,
    normalize_field_name, detect_drift, DriftError,
    SpecStore, FileSpecStore, MemorySpecStore,
    quarantine_rows, write_quarantine, read_quarantine, infer_model_source,
    register_transform, apply_transform, available_transforms, TransformError,
    register_enrichment, available_enrichments, EnrichmentError,
    register_batch_enrichment, available_batch_enrichments, BatchEnrichmentError,
    register_expander, available_expanders, split_rows, ExpansionError,
    register_column_step, available_column_steps, ColumnStepError,
    LLMProvider, resolve_provider,
    AnthropicProvider, OpenAIProvider, LocalProvider, FakeProvider,
    from_csv, from_records, from_json, from_excel, SourceData,
)
```

---

## License

MIT.
