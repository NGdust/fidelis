"""fidelis — ingest messy table-like feeds from any source.

"Feeds, not files" — any source, one contract. The LLM generates a versioned
spec once, keyed by the field signature; after that the mapping is
deterministic, LLM-free, with built-in validation and schema drift detection.
"""

from __future__ import annotations

from .column import (
    ColumnStepError,
    available_column_steps,
    register_column_step,
)
from .drift import DriftError, detect_drift
from .infer_model import infer_model_source
from .enrichment import (
    BatchEnrichmentError,
    EnrichmentError,
    available_batch_enrichments,
    available_enrichments,
    register_batch_enrichment,
    register_enrichment,
)
from .expand import (
    ExpansionError,
    available_expanders,
    register_expander,
    split_rows,
)
from .llm import (
    AnthropicProvider,
    FakeProvider,
    LLMProvider,
    LocalProvider,
    OpenAIProvider,
    resolve_provider,
)
from .parser import Parser, SpecNotFoundError
from .quarantine import quarantine_rows, read_quarantine, write_quarantine
from .store import FileSpecStore, MemorySpecStore, SpecStore
from .result import Coverage, DriftReport, DuplicateRow, ParseResult, RowError
from .sources import SourceData, from_csv, from_excel, from_json, from_records
from .spec import (
    Condition,
    DedupSpec,
    ExpandStep,
    Mapping,
    ParsingSpec,
    Rule,
    Spec,
    UnpivotSpec,
    compute_signature,
    find_spec_by_signature,
    normalize_field_name,
)
from .transforms import (
    TransformError,
    apply_transform,
    available_transforms,
    register_transform,
)

__version__ = "0.1.1"

__all__ = [
    # Entry point
    "Parser",
    "ParseResult",
    "RowError",
    "DriftReport",
    "DuplicateRow",
    "Coverage",
    "SpecNotFoundError",
    # Model inference
    "infer_model_source",
    # Quarantine round-trip
    "quarantine_rows",
    "write_quarantine",
    "read_quarantine",
    # Sources
    "SourceData",
    "from_csv",
    "from_records",
    "from_json",
    "from_excel",
    # Spec
    "Spec",
    "Mapping",
    "ParsingSpec",
    "DedupSpec",
    "UnpivotSpec",
    "Condition",
    "Rule",
    "ExpandStep",
    "compute_signature",
    "find_spec_by_signature",
    "normalize_field_name",
    # Spec storage
    "SpecStore",
    "FileSpecStore",
    "MemorySpecStore",
    # Drift
    "detect_drift",
    "DriftError",
    # Transforms
    "register_transform",
    "apply_transform",
    "available_transforms",
    "TransformError",
    # Enrichment
    "register_enrichment",
    "available_enrichments",
    "EnrichmentError",
    "register_batch_enrichment",
    "available_batch_enrichments",
    "BatchEnrichmentError",
    # Row expansion (fan-out)
    "register_expander",
    "available_expanders",
    "split_rows",
    "ExpansionError",
    # Whole-column steps
    "register_column_step",
    "available_column_steps",
    "ColumnStepError",
    # LLM
    "LLMProvider",
    "resolve_provider",
    "AnthropicProvider",
    "OpenAIProvider",
    "LocalProvider",
    "FakeProvider",
    "__version__",
]
