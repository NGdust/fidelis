"""Schema drift detection between a spec and the actual set of source fields."""

from __future__ import annotations

from .result import DriftReport
from .spec import Spec, normalize_field_name


def detect_drift(spec: Spec, field_names: list[str]) -> DriftReport:
    """Compare the source fields against what the spec expects.

    Drift = an unknown field appeared and/or a field expected by the spec
    disappeared. The comparison runs on normalized names, but the report keeps
    the original names.
    """

    actual_norm = {normalize_field_name(f): f for f in field_names}
    expected = spec.source_fields  # normalized source fields

    new_norm = set(actual_norm) - expected
    missing_norm = expected - set(actual_norm)

    new_fields = sorted(actual_norm[n] for n in new_norm)
    # For disappeared fields, return the original source strings from the mappings.
    missing_sources = {
        normalize_field_name(m.source): m.source for m in spec.mappings if m.source
    }
    missing_fields = sorted(missing_sources.get(n, n) for n in missing_norm)

    has_drift = bool(new_fields or missing_fields)
    return DriftReport(
        has_drift=has_drift,
        new_fields=new_fields,
        missing_fields=missing_fields,
    )


class DriftError(ValueError):
    """Raised when ``on_unknown_column="error"`` and drift is detected."""

    def __init__(self, report: DriftReport):
        self.report = report
        super().__init__(report.describe())
