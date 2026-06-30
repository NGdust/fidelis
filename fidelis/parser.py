"""Public facade: ``Parser`` — one entry point, one ``parse()`` for any source."""

from __future__ import annotations

import os
from datetime import date
from typing import Optional, Sequence, Type, Union

from pydantic import BaseModel

from .column import available_column_steps, resolve_column_step
from .dedup import dedup_rows
from .drift import DriftError, detect_drift
from .enrichment import (
    available_batch_enrichments,
    available_enrichments,
    resolve_batch_enrichment,
    resolve_enrichment,
)
from .expand import available_expanders, resolve_expander, split_rows
from .llm.base import LLMProvider, resolve_provider
from .llm.inference import generate_spec as _llm_generate_spec
from .mapping import (
    BatchEnricher,
    Enricher,
    Expander,
    apply_batch,
    apply_column_steps,
    map_records,
)
from .result import DriftReport, ParseResult
from .sources import from_csv, from_excel, from_json, from_records
from .sources.base import DEFAULT_SAMPLE_SIZE, SourceData
from .sources.connectors import kind_from_name, resolve_source
from .spec import (
    OnUnknownColumn,
    ParsingSpec,
    Spec,
    compute_signature,
    normalize_field_name,
)
from .store import FileSpecStore, SpecStore
from .transforms import available_transforms, parse_transform_spec
from .validate import resolve_model, validate_rows

Source = Union[str, os.PathLike, bytes, list, dict]
Kind = Optional[str]


class SpecNotFoundError(RuntimeError):
    """No spec exists and no LLM is configured to generate one."""


class Parser:
    """Accepts any table-like source and coerces it to the target model.

    The behavior is deterministic and does NOT call the LLM when a matching
    spec already exists.
    """

    def __init__(
        self,
        target_model: Type[BaseModel],
        *,
        spec_store: Union[SpecStore, str, os.PathLike] = "specs/",
        llm: Optional[Union[str, LLMProvider]] = None,
        on_unknown_column: OnUnknownColumn = "error",
        confidence_threshold: float = 0.8,
        sample_size: int = DEFAULT_SAMPLE_SIZE,
        strict: bool = False,
        llm_options: Optional[dict] = None,
        domain_hints: Optional[Union[str, dict]] = None,
        context: object = None,
        expand: Optional[Sequence[object]] = None,
        enrich: Optional[Sequence[object]] = None,
        batch_enrich: Optional[Sequence[object]] = None,
        column_steps: Optional[dict] = None,
        dedup_key: Optional[Union[str, Sequence[str]]] = None,
        dedup_keep: str = "first",
    ):
        self.model = resolve_model(target_model)
        # Where specs are read/written. A path (str/PathLike) is sugar for a
        # FileSpecStore; pass a SpecStore to keep specs in S3, a DB, etc.
        self._store: SpecStore = (
            spec_store
            if isinstance(spec_store, SpecStore)
            else FileSpecStore(spec_store)
        )
        self.on_unknown_column: OnUnknownColumn = on_unknown_column
        self.confidence_threshold = confidence_threshold
        self.sample_size = sample_size
        self.strict = strict
        # Typed domain context handed to the LLM during spec generation (allowed
        # values, units, ranges…). Free text or a dict; never used at parse time.
        self.domain_hints = domain_hints
        # Run context passed into hooks (transforms/enrichers/expanders) that
        # declare a `context` parameter — lookups, thresholds, config.
        self.context = context
        self._llm_spec = llm
        self._llm_options = llm_options or {}
        self._provider: Optional[LLMProvider] = (
            resolve_provider(llm, **self._llm_options) if llm is not None else None
        )
        # Row expanders fan one source row out into many; resolved up front.
        self._expand: list[Expander] = [
            resolve_expander(ref) for ref in (expand or [])
        ]
        # Post-mapping enrichers, resolved (name + callable) up front so an
        # unknown name fails loudly at construction, not silently per row.
        self._enrich: list[Enricher] = [
            resolve_enrichment(ref) for ref in (enrich or [])
        ]
        # Batch enrichers run once over the whole set of clean rows (after the
        # per-row enrichers), enabling a single bulk lookup instead of N calls.
        self._batch_enrich: list[BatchEnricher] = [
            resolve_batch_enrichment(ref) for ref in (batch_enrich or [])
        ]
        # Whole-column rewrite steps: (field, name, fn) resolved up front.
        self._column_steps: list = [
            (field, *resolve_column_step(ref))
            for field, ref in (column_steps or {}).items()
        ]
        # Dedup key: model fields whose combination identifies a row. Validated
        # up front so a typo'd field name fails at construction.
        self._dedup_key: list[str] = (
            [dedup_key] if isinstance(dedup_key, str) else list(dedup_key or [])
        )
        if dedup_keep not in ("first", "last"):
            raise ValueError(
                f"dedup_keep must be 'first' or 'last', got {dedup_keep!r}"
            )
        self._dedup_keep = dedup_keep
        unknown = [f for f in self._dedup_key if f not in self.model.model_fields]
        if unknown:
            raise ValueError(
                f"dedup_key field(s) not on {self.model.__name__}: {unknown}"
            )

    # ------------------------------------------------------------------ #
    # Source loading
    # ------------------------------------------------------------------ #

    def _load_source(
        self, source: Source, kind: Kind, parsing: Optional[ParsingSpec] = None
    ) -> SourceData:
        """Pick an adapter by ``kind`` or by the source's type/extension."""

        if kind is None:
            kind = self._infer_kind(source)

        if kind == "records":
            # A single dict is one record, not an iterable of records.
            recs = [source] if isinstance(source, dict) else source
            return from_records(recs, sample_size=self.sample_size)
        if kind == "json":
            return from_json(source, sample_size=self.sample_size)
        if kind == "excel":
            return from_excel(source, sample_size=self.sample_size)
        if kind == "csv":
            kwargs = {}
            if parsing:
                kwargs = {
                    "encoding": parsing.encoding,
                    "delimiter": parsing.delimiter,
                    "quote_char": parsing.quote_char,
                }
            return from_csv(source, sample_size=self.sample_size, **kwargs)
        raise ValueError(f"Unknown source kind: {kind!r}")

    @staticmethod
    def _infer_kind(source: Source) -> str:
        if isinstance(source, (list, dict)):
            return "records"
        if isinstance(source, (str, os.PathLike)):
            # A string without a known extension is treated as CSV text.
            return kind_from_name(os.fspath(source)) or "csv"
        if isinstance(source, bytes):
            return "csv"
        raise ValueError(f"Could not determine source type: {type(source)!r}")

    @staticmethod
    def _is_rereadable(source: Source) -> bool:
        return isinstance(source, (str, os.PathLike, bytes, list, dict))

    # ------------------------------------------------------------------ #
    # Public methods
    # ------------------------------------------------------------------ #

    def parse(self, source: Source, *, kind: Kind = None) -> ParseResult:
        """Parse a source into a ``ParseResult`` (valid_rows + errors + reports)."""

        from .runtime import reset_context, set_context

        token = set_context(self.context)
        try:
            return self._parse(source, kind=kind)
        finally:
            reset_context(token)

    def _parse(self, source: Source, *, kind: Kind = None) -> ParseResult:

        # Resolve remote/compressed sources (URL, .gz) into in-memory bytes first.
        source, kind = resolve_source(source, kind)
        data = self._load_source(source, kind)
        signature = compute_signature(data.field_names)
        spec = self._store.get(signature)

        spec_generated = False
        drift = DriftReport()

        if spec is None:
            # No exact match — maybe this is drift of a familiar source?
            candidate = self._store.find_drift_candidate(data.field_names)
            if candidate is not None:
                drift = detect_drift(candidate, data.field_names)
                spec, data = self._handle_drift(candidate, data, drift, source, kind)
            else:
                spec = self._generate_and_save(data, signature)
                spec_generated = True
        else:
            # Exact signature match — there can be no drift.
            # If the spec carries parsing info and the source is text and
            # re-readable, re-read it with the dialect from the spec
            # (faithful Stage 1).
            if (
                data.raw_kind == "text"
                and spec.parsing
                and self._is_rereadable(source)
                and (spec.parsing.delimiter or spec.parsing.encoding)
            ):
                data = self._load_source(source, kind or "csv", parsing=spec.parsing)

        # Steps come from the spec first (per-source contract); the Parser-level
        # args act as a global default for specs that don't declare a step.
        expand = self._effective_expand(spec)
        enrich = self._effective_enrich(spec)
        batch_enrich = self._effective_batch_enrich(spec)
        column_steps = self._effective_column_steps(spec)
        dedup_key, dedup_keep = self._effective_dedup(spec)

        mapped = map_records(spec, data.records, enrich, expand)
        if batch_enrich or column_steps:
            # These need the whole set at once, so materialize here (only when
            # configured — otherwise the pipeline stays lazy).
            mapped = apply_batch(list(mapped), batch_enrich)
            mapped = apply_column_steps(mapped, column_steps)
        valid, errors, coverage = validate_rows(self.model, mapped, strict=self.strict)
        duplicates = []
        if dedup_key:
            valid, duplicates = dedup_rows(valid, dedup_key, dedup_keep)
        return ParseResult(
            valid_rows=valid,
            errors=errors,
            spec_used=spec,
            drift_report=drift,
            spec_generated=spec_generated,
            duplicates=duplicates,
            coverage=coverage,
        )

    def generate_spec(self, source: Source, *, kind: Kind = None) -> Spec:
        """Only generate and return a draft spec (for a review flow in CI)."""

        source, kind = resolve_source(source, kind)
        data = self._load_source(source, kind)
        signature = compute_signature(data.field_names)
        return self._generate_and_save(data, signature, save=True)

    def validate_spec(self, spec: Union[Spec, str, os.PathLike]) -> list[str]:
        """Validate a spec. Returns a list of problems (empty = ok)."""

        if not isinstance(spec, Spec):
            spec = Spec.load(spec)

        problems: list[str] = []
        valid_targets = set(self.model.model_fields)
        seen_targets: set[str] = set()

        for m in spec.mappings:
            for tgt in m.target_fields:
                if tgt not in valid_targets:
                    problems.append(
                        f"target {tgt!r} is not a field of model "
                        f"{self.model.__name__}"
                    )
                if tgt in seen_targets:
                    problems.append(f"target {tgt!r} is mapped more than once")
                seen_targets.add(tgt)
            if not m.targets and not m.source and m.value is None:
                problems.append(f"mapping to {m.target!r} has no source and no value")
            label = m.target or "/".join(m.target_fields)
            name, _arg = parse_transform_spec(m.transform)
            if name and name not in available_transforms():
                problems.append(
                    f"unknown transform {name!r} in mapping to {label!r}"
                )
            if not 0.0 <= m.confidence <= 1.0:
                problems.append(
                    f"confidence out of [0,1] in mapping to {label!r}: {m.confidence}"
                )

        # Rule mappings: their target fields and transforms must be valid too
        # (a rule's targets count as covered, since each rule emits a record).
        for r in spec.rules:
            for rm in r.mappings:
                for tgt in rm.target_fields:
                    if tgt not in valid_targets:
                        problems.append(
                            f"rule target {tgt!r} is not a field of model "
                            f"{self.model.__name__}"
                        )
                    seen_targets.add(tgt)
                rname, _ra = parse_transform_spec(rm.transform)
                if rname and rname not in available_transforms():
                    problems.append(f"unknown transform {rname!r} in a rule mapping")

        # Required model fields without a default must be covered by a mapping
        # (base or any rule).
        for fname, finfo in self.model.model_fields.items():
            if finfo.is_required() and fname not in seen_targets:
                problems.append(
                    f"required model field {fname!r} is not covered by any mapping"
                )

        # Record-level steps: referenced names must be registered, dedup key
        # fields must exist on the model.
        for step in spec.expand:
            if step.expander and step.expander not in available_expanders():
                problems.append(f"unknown expander {step.expander!r}")
            if step.field and step.field not in valid_targets:
                problems.append(
                    f"expand field {step.field!r} is not a field of model "
                    f"{self.model.__name__}"
                )
        for name in spec.enrich:
            if name not in available_enrichments():
                problems.append(f"unknown enrichment {name!r}")
        for name in spec.batch_enrich:
            if name not in available_batch_enrichments():
                problems.append(f"unknown batch enrichment {name!r}")
        for field, name in spec.column_steps.items():
            if name not in available_column_steps():
                problems.append(f"unknown column step {name!r}")
            if field not in valid_targets:
                problems.append(
                    f"column step field {field!r} is not a field of model "
                    f"{self.model.__name__}"
                )
        if spec.dedup:
            if spec.dedup.keep not in ("first", "last"):
                problems.append(f"dedup.keep must be 'first' or 'last': {spec.dedup.keep!r}")
            for field_name in spec.dedup.key:
                if field_name not in valid_targets:
                    problems.append(
                        f"dedup key field {field_name!r} is not a field of model "
                        f"{self.model.__name__}"
                    )
        return problems

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _effective_expand(self, spec: Spec) -> list[Expander]:
        """Spec expand steps take precedence; else the Parser default."""

        if spec.expand:
            return [self._build_expander(step) for step in spec.expand]
        return self._expand

    @staticmethod
    def _build_expander(step) -> Expander:
        """Turn an ``ExpandStep`` into a ``(name, fn)`` expander."""

        if step.expander:
            return resolve_expander(step.expander)
        fn = split_rows(
            step.field, step.delimiter, strip=step.strip, drop_empty=step.drop_empty
        )
        return (f"split:{step.field}", fn)

    def _effective_enrich(self, spec: Spec) -> list[Enricher]:
        """Spec enrichers (by name) take precedence; else the Parser default."""

        if spec.enrich:
            return [resolve_enrichment(name) for name in spec.enrich]
        return self._enrich

    def _effective_column_steps(self, spec: Spec) -> list:
        if spec.column_steps:
            return [
                (field, *resolve_column_step(name))
                for field, name in spec.column_steps.items()
            ]
        return self._column_steps

    def _effective_batch_enrich(self, spec: Spec) -> list[BatchEnricher]:
        if spec.batch_enrich:
            return [resolve_batch_enrichment(name) for name in spec.batch_enrich]
        return self._batch_enrich

    def _effective_dedup(self, spec: Spec) -> tuple[list[str], str]:
        if spec.dedup and spec.dedup.key:
            key = list(spec.dedup.key)
            unknown = [f for f in key if f not in self.model.model_fields]
            if unknown:
                raise ValueError(
                    f"dedup key field(s) not on {self.model.__name__}: {unknown}"
                )
            return key, spec.dedup.keep
        return self._dedup_key, self._dedup_keep

    def _require_provider(self) -> LLMProvider:
        if self._provider is None:
            raise SpecNotFoundError(
                "No matching spec was found and no LLM is configured. "
                "Pass llm=\"anthropic:claude-opus-4-8\" to Parser(...) "
                "or add a spec to the spec store."
            )
        return self._provider

    def _generate_and_save(
        self, data: SourceData, signature: str, *, save: bool = True
    ) -> Spec:
        provider = self._require_provider()
        parsing_hints = self._parsing_from_meta(data)
        spec = _llm_generate_spec(
            provider,
            field_names=data.field_names,
            sample=data.sample,
            model=self.model,
            raw_kind=data.raw_kind,
            generated_at=date.today().isoformat(),
            confidence_threshold=self.confidence_threshold,
            parsing_hints=parsing_hints,
            domain_hints=self.domain_hints,
        )
        if save:
            self._store.save(spec)
        return spec

    @staticmethod
    def _parsing_from_meta(data: SourceData) -> Optional[ParsingSpec]:
        if data.raw_kind != "text":
            return None
        meta = data.meta
        if not meta.get("delimiter"):
            return None
        return ParsingSpec(
            delimiter=meta.get("delimiter"),
            encoding=meta.get("encoding"),
            quote_char=meta.get("quote_char"),
        )

    def _handle_drift(
        self,
        spec: Spec,
        data: SourceData,
        drift: DriftReport,
        source: Source,
        kind: Kind,
    ) -> tuple[Spec, SourceData]:
        policy: OnUnknownColumn = spec.on_unknown_column or self.on_unknown_column
        drift.action = policy

        if policy == "ignore":
            return spec, data
        if policy == "error":
            raise DriftError(drift)
        if policy == "regenerate":
            spec = self._regenerate_missing(spec, data)
            return spec, data
        raise ValueError(f"Unknown on_unknown_column policy: {policy!r}")

    def _regenerate_missing(self, spec: Spec, data: SourceData) -> Spec:
        """Use the LLM to generate additional mappings for new/uncovered target fields."""

        provider = self._require_provider()
        fresh = _llm_generate_spec(
            provider,
            field_names=data.field_names,
            sample=data.sample,
            model=self.model,
            raw_kind=data.raw_kind,
            confidence_threshold=self.confidence_threshold,
            domain_hints=self.domain_hints,
        )
        existing_targets = {t for m in spec.mappings for t in m.target_fields}
        merged = list(spec.mappings)
        for m in fresh.mappings:
            if m.target not in existing_targets:
                merged.append(m)
        spec.mappings = merged
        spec.signature = compute_signature(data.field_names)
        spec.generated_by = provider.model
        self._store.save(spec)
        return spec
