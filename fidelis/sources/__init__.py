"""Input adapters. All return a unified :class:`SourceData`."""

from .base import DEFAULT_SAMPLE_SIZE, SourceData
from .csv_source import from_csv
from .excel_source import from_excel
from .json_source import from_json
from .records_source import from_records

__all__ = [
    "SourceData",
    "DEFAULT_SAMPLE_SIZE",
    "from_csv",
    "from_records",
    "from_json",
    "from_excel",
]
