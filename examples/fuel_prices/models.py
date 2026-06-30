"""Canonical target model for the fuel-prices example.

Every vendor feed — whatever its columns, units, or language — is coerced into
this one contract. Fields a vendor may omit have defaults; fields we add from
our own database are Optional and start as None.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel


class FuelPrice(BaseModel):
    # --- mapped from the vendor CSV ---
    airport_code: str                  # "KJFK" or "JFK" — whatever the vendor sent
    code_type: str = "icao"            # icao | iata — stamped per vendor via `value`
    vendor: str
    product: str = "Jet A-1"           # default when the vendor omits it
    price: float
    currency: str = "USD"              # default
    unit_volume: str = "liter"         # liter | usg — varies by vendor
    valid_from: date

    # --- enriched from our DB (batch, one query per file) ---
    airport_id: Optional[int] = None
    vendor_id: Optional[int] = None
    price_usd_per_liter: Optional[float] = None
