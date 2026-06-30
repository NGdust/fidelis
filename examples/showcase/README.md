# Showcase

Worked examples, simplest first. Each folder is self-contained — a CSV (the
dirty input), a `run.py` (the script), and a `*.yaml` (the spec that drives the
deterministic parse). No LLM and no network: the specs already exist, so each
example just maps and validates.

Run any of them from the repo root:

```bash
python examples/showcase/01_basic/run.py
```

| Folder | Demonstrates |
|--------|--------------|
| `01_basic`      | rename columns onto a model, per-cell transforms, a `value` default |
| `02_enrichment` | a custom enricher (sees the raw row) + a batch enricher (one pass over the feed) |
| `03_expand`     | row fan-out — one cell of `A|B|C` becomes one record each; separator lives in the spec |
| `04_unpivot`    | wide → tall — repeating `Q1/Q2/Q3` column groups become one record per quarter |
| `05_rules`      | conditional records — one row emits a `transfer` always and a `fee` only when present |
| `06_advanced`   | the whole pipeline at once: `skip_when`, a multi-target split, multi-format dates, `clip`, a context-aware whole-column step, and a quarantined bad row dropping `coverage` below 1.0 |
