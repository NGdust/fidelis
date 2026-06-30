"""Spec model, field fingerprint, YAML load/save.

A spec is a human-readable, versioned artifact (see section 7 of the PRD). It
lives in the user's repository (``specs/`` by default). A spec's identity is tied
to the signature of the source fields (``signature``), not to the file name.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

import yaml
from pydantic import BaseModel, Field, model_validator

OnUnknownColumn = Literal["ignore", "error", "regenerate"]
MappingStatus = Literal["ok", "needs_review"]

_WS_RE = re.compile(r"[\s_]+")


def normalize_field_name(name: str) -> str:
    """Normalize a field name: trim, lowercase, collapse spaces/underscores."""

    return _WS_RE.sub(" ", str(name).strip().lower()).strip()


def compute_signature(field_names: Iterable[str]) -> str:
    """Hash of the normalized set of source field names.

    Field order and case do not affect the signature — the same spec applies to
    both CSV and JSON with the same fields by meaning. Returns a short hex (6 chars).
    """

    normalized = sorted({normalize_field_name(f) for f in field_names})
    digest = hashlib.sha256("\x00".join(normalized).encode("utf-8")).hexdigest()
    return digest[:6]


class ParsingSpec(BaseModel):
    """Stage 1 — structural dialect (text sources only)."""

    delimiter: Optional[str] = None
    encoding: Optional[str] = None
    quote_char: Optional[str] = None


class Mapping(BaseModel):
    """A single mapping of a source field to target field(s) of the target model.

    ``source`` and ``value`` are complementary:
    - ``source`` only: take the source cell, run ``transform``.
    - ``value`` only (no ``source``): a constant — the target is always ``value``.
    - both: ``value`` is the default used when the source cell is empty/missing.

    Use ``targets`` instead of ``target`` for a **multi-target** transform — the
    transform returns a dict and ``targets`` maps its keys to model fields, e.g.
    keep both the converted and original value::

        {source: WEIGHT, transform: lbs_to_kg,
         targets: {kg: weight_kg, original: weight_original, unit: weight_unit}}
    """

    target: Optional[str] = None
    targets: Optional[dict[str, str]] = None  # transform-output key -> model field
    source: Optional[str] = None
    value: Optional[Any] = None
    transform: Optional[str] = None
    confidence: float = 1.0
    status: MappingStatus = "ok"

    @model_validator(mode="after")
    def _target_xor_targets(self) -> "Mapping":
        if bool(self.target) == bool(self.targets):
            raise ValueError("a mapping needs exactly one of 'target' or 'targets'")
        if self.targets and not (self.source and self.transform):
            raise ValueError("a multi-target ('targets') mapping needs source + transform")
        return self

    @property
    def target_fields(self) -> list[str]:
        """The model field(s) this mapping writes to."""

        return [self.target] if self.target else list(self.targets.values())


def _mapping_to_yaml(m: "Mapping") -> dict:
    """A single mapping as a clean YAML dict (shared by spec + rule mappings)."""

    entry: dict = {}
    if m.target:
        entry["target"] = m.target
    if m.targets:
        entry["targets"] = dict(m.targets)
    if m.source:
        entry["source"] = m.source
    if m.value is not None:
        entry["value"] = m.value
    if m.transform:
        entry["transform"] = m.transform
    entry["confidence"] = round(m.confidence, 4)
    entry["status"] = m.status
    return entry


class Condition(BaseModel):
    """A predicate on a source column, used by a :class:`Rule`'s ``when``."""

    field: str
    op: Literal["not_empty", "empty", "eq", "ne", "in", "gt", "lt", "ge", "le"] = "not_empty"
    value: Optional[Any] = None


class Rule(BaseModel):
    """A conditional record: emit one record (base mappings + these) when
    ``when`` holds. One source row may fire several rules → several records."""

    when: Condition
    mappings: list["Mapping"] = Field(default_factory=list)


class UnpivotSpec(BaseModel):
    """Repeating column groups → one record per group (a pre-mapping unpivot).

    ``columns`` maps a canonical name to a template with ``{i}``; for each index
    the group's columns are renamed to the canonical names so the normal mappings
    can reference them, e.g.::

        unpivot:
          count: 3                       # indices 1..3 (or list them in `index`)
          index_field: quarter           # put the index into this source column
          columns:
            PRICE: "Q{i}_PRICE"
            QTY:   "Q{i}_QTY"
    """

    columns: dict[str, str]                 # canonical name -> "Q{i}_PRICE"
    count: Optional[int] = None             # 1..count
    index: Optional[list] = None            # explicit indices instead of count
    index_field: Optional[str] = None       # source column to receive the index
    drop_empty: bool = True                 # skip an index whose group is all-empty
    keep_others: bool = True                # keep non-group columns in each record

    @model_validator(mode="after")
    def _count_xor_index(self) -> "UnpivotSpec":
        if (self.count is None) == (self.index is None):
            raise ValueError("unpivot needs exactly one of 'count' or 'index'")
        if not self.columns:
            raise ValueError("unpivot needs at least one column")
        return self

    @property
    def indices(self) -> list:
        return list(self.index) if self.index is not None else list(range(1, self.count + 1))


class ExpandStep(BaseModel):
    """One row-expansion step: either a declarative column split, or a named
    custom expander. Exactly one of ``field`` / ``expander`` must be set.

    Declarative split:  ``{field: airport_code, delimiter: "|"}``
    Custom expander:     ``{expander: by_region}``
    """

    field: Optional[str] = None       # column to split (a target model field)
    delimiter: str = ","              # list separator within that cell
    strip: bool = True                # trim each value
    drop_empty: bool = True           # drop empty values
    expander: Optional[str] = None    # OR a registered custom expander name

    @model_validator(mode="after")
    def _exactly_one(self) -> "ExpandStep":
        if bool(self.field) == bool(self.expander):
            raise ValueError(
                "an expand step needs exactly one of 'field' (split) or "
                "'expander' (custom)"
            )
        return self


class DedupSpec(BaseModel):
    """Row-dedup config: model field(s) forming the key + which occurrence to keep."""

    key: list[str] = Field(default_factory=list)
    keep: Literal["first", "last"] = "first"


class Spec(BaseModel):
    """Declarative spec — an ingestion contract for a single source format."""

    version: int = 1
    generated_by: str = "human"
    generated_at: Optional[str] = None
    signature: str
    parsing: Optional[ParsingSpec] = None
    #: Pre-mapping unpivot of repeating column groups (``None`` = off).
    unpivot: Optional[UnpivotSpec] = None
    mappings: list[Mapping] = Field(default_factory=list)
    #: Conditional records: when set, one source row emits a record per firing
    #: rule (base ``mappings`` + the rule's mappings). Empty = single record.
    rules: list[Rule] = Field(default_factory=list)
    #: Whole-column rewrite steps (model field -> registered column step name),
    #: applied across all rows before validation.
    column_steps: dict[str, str] = Field(default_factory=dict)
    #: Skip a source row entirely when any of these conditions holds (e.g. a
    #: status in a stop-list). Skipped rows produce no record.
    skip_when: list[Condition] = Field(default_factory=list)
    #: Row expansion steps applied after mapping — one source row may fan out into
    #: many records (a declarative column split, or a named custom expander).
    expand: list[ExpandStep] = Field(default_factory=list)
    #: Record-level steps applied after mapping, referenced by registered name
    #: (see fidelis.register_enrichment / register_batch_enrichment). Human-added;
    #: the LLM never fills these.
    enrich: list[str] = Field(default_factory=list)
    batch_enrich: list[str] = Field(default_factory=list)
    #: Dedup applied after validation. ``None`` = no dedup for this source.
    dedup: Optional[DedupSpec] = None
    on_unknown_column: Optional[OnUnknownColumn] = None

    # ------------------------------------------------------------------ #
    # Convenient derived values
    # ------------------------------------------------------------------ #

    @property
    def source_fields(self) -> set[str]:
        """Normalized source fields the spec expects (constants have no source)."""

        return {normalize_field_name(m.source) for m in self.mappings if m.source}

    @property
    def has_needs_review(self) -> bool:
        """Whether the spec has any mappings with status ``needs_review``."""

        return any(m.status == "needs_review" for m in self.mappings)

    # ------------------------------------------------------------------ #
    # Serialization
    # ------------------------------------------------------------------ #

    def to_yaml_dict(self) -> dict:
        """Clean dict for YAML — without ``None`` fields, in human-readable order."""

        data: dict = {
            "version": self.version,
            "generated_by": self.generated_by,
        }
        if self.generated_at:
            data["generated_at"] = self.generated_at
        data["signature"] = self.signature
        if self.parsing:
            parsing = {k: v for k, v in self.parsing.model_dump().items() if v is not None}
            if parsing:
                data["parsing"] = parsing
        if self.unpivot:
            up: dict = {"columns": dict(self.unpivot.columns)}
            if self.unpivot.count is not None:
                up["count"] = self.unpivot.count
            if self.unpivot.index is not None:
                up["index"] = list(self.unpivot.index)
            if self.unpivot.index_field:
                up["index_field"] = self.unpivot.index_field
            if not self.unpivot.drop_empty:
                up["drop_empty"] = False
            if not self.unpivot.keep_others:
                up["keep_others"] = False
            data["unpivot"] = up
        data["mappings"] = [_mapping_to_yaml(m) for m in self.mappings]
        if self.rules:
            data["rules"] = []
            for r in self.rules:
                when: dict = {"field": r.when.field, "op": r.when.op}
                if r.when.value is not None:
                    when["value"] = r.when.value
                data["rules"].append(
                    {"when": when, "mappings": [_mapping_to_yaml(m) for m in r.mappings]}
                )
        if self.expand:
            steps: list[dict] = []
            for s in self.expand:
                if s.expander:
                    steps.append({"expander": s.expander})
                    continue
                step: dict = {"field": s.field, "delimiter": s.delimiter}
                if not s.strip:
                    step["strip"] = False
                if not s.drop_empty:
                    step["drop_empty"] = False
                steps.append(step)
            data["expand"] = steps
        if self.enrich:
            data["enrich"] = list(self.enrich)
        if self.batch_enrich:
            data["batch_enrich"] = list(self.batch_enrich)
        if self.column_steps:
            data["column_steps"] = dict(self.column_steps)
        if self.skip_when:
            data["skip_when"] = [
                {"field": c.field, "op": c.op, **({"value": c.value} if c.value is not None else {})}
                for c in self.skip_when
            ]
        if self.dedup and self.dedup.key:
            data["dedup"] = {"key": list(self.dedup.key), "keep": self.dedup.keep}
        if self.on_unknown_column:
            data["on_unknown_column"] = self.on_unknown_column
        return data

    def dump_yaml(self) -> str:
        return yaml.safe_dump(
            self.to_yaml_dict(),
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )

    def save(self, spec_dir: str | os.PathLike) -> Path:
        """Write the spec to ``<spec_dir>/spec_<signature>.yaml``.

        Lookup scans by ``signature`` (not by file name), so a hand-written spec
        may use any readable file name — the name is just a human label."""

        directory = Path(spec_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"spec_{self.signature}.yaml"
        path.write_text(self.dump_yaml(), encoding="utf-8")
        return path

    @classmethod
    def from_yaml(cls, text: str) -> "Spec":
        return cls.model_validate(yaml.safe_load(text))

    @classmethod
    def load(cls, path: str | os.PathLike) -> "Spec":
        return cls.from_yaml(Path(path).read_text(encoding="utf-8"))


def find_spec_by_signature(spec_dir: str | os.PathLike, signature: str) -> Optional[Spec]:
    """Find a spec in the directory by ``signature``. ``None`` if not found."""

    directory = Path(spec_dir)
    if not directory.exists():
        return None
    for path in sorted(directory.glob("*.y*ml")):
        try:
            spec = Spec.load(path)
        except Exception:
            continue
        if spec.signature == signature:
            return spec
    return None


def best_drift_candidate(
    specs: Iterable[Spec],
    field_names: Iterable[str],
    *,
    min_similarity: float = 0.5,
) -> Optional[Spec]:
    """Pick the spec whose source fields overlap ``field_names`` the most.

    Uses the Jaccard measure on normalized source fields and only returns a
    candidate above ``min_similarity``. Storage-agnostic: works over any iterable
    of specs, so a custom :class:`~fidelis.SpecStore` can reuse it.
    """

    actual = {normalize_field_name(f) for f in field_names}
    if not actual:
        return None

    best: Optional[Spec] = None
    best_score = 0.0
    for spec in specs:
        expected = spec.source_fields
        if not expected:
            continue
        union = expected | actual
        score = len(expected & actual) / len(union) if union else 0.0
        if score > best_score:
            best_score = score
            best = spec
    if best is not None and best_score >= min_similarity:
        return best
    return None


def find_drift_candidate(
    spec_dir: str | os.PathLike,
    field_names: Iterable[str],
    *,
    min_similarity: float = 0.5,
) -> Optional[Spec]:
    """Find a drift candidate among the spec files in ``spec_dir``.

    Adding or removing a field changes the ``signature``, so the exact
    lookup misses. This distinguishes drift of a familiar source from a brand-new
    format by field-set overlap.
    """

    return best_drift_candidate(
        iter_specs(spec_dir), field_names, min_similarity=min_similarity
    )


def iter_specs(spec_dir: str | os.PathLike) -> list[Spec]:
    """Load all valid specs from the directory."""

    directory = Path(spec_dir)
    if not directory.exists():
        return []
    specs: list[Spec] = []
    for path in sorted(directory.glob("*.y*ml")):
        try:
            specs.append(Spec.load(path))
        except Exception:
            continue
    return specs
