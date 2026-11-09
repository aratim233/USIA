"""
Request bodies accepted by the API.

FastAPI checks every incoming JSON body against these classes, so a missing
name or a wrong date format is rejected with a clear 422 error before any of
our code runs.
"""
from datetime import date

from pydantic import BaseModel, Field


# ---- products --------------------------------------------------------------

class ProductIn(BaseModel):
    code: str | None = None         # leave empty and the next P-number (P01, P02 ...) is assigned
    name: str
    vendor: str | None = None
    description: str | None = None


class VersionIn(BaseModel):
    version: str
    placed_on_market: date | None = None
    support_period_end: date | None = None


# ---- customers -------------------------------------------------------------

class CustomerIn(BaseModel):
    name: str
    country: str | None = None


class CustomerUpdate(BaseModel):
    name: str
    country: str | None = None


class SolutionIn(BaseModel):
    name: str


class SolutionVersionIn(BaseModel):
    version: str                      # major version, e.g. 1.0 or 2.0
    delivered_on: date | None = None  # kept for older clients; deliveries now carry the date
    support_end: date | None = None   # support end agreed with the customer for this version


class DeliveryIn(BaseModel):
    delivered_on: date | None = None  # defaults to today
    note: str | None = None           # e.g. "Minor update: P02 1.27 -> 1.28"


class ItemIn(BaseModel):
    product_version_id: int
    quantity: int = 1


# ---- retention policies ----------------------------------------------------

class PolicyIn(BaseModel):
    code: str                        # short ID such as EU_CRA or JC_STAR
    name: str
    legal_basis: str
    description: str | None = None
    min_years: int = Field(10, ge=0, le=100)
    clock_start: str = "placed_on_market"
    use_support_period_if_longer: bool = True
    warn_days_before_expiry: int = Field(365, ge=0)
    apply_by_default: bool = False   # give it to every new customer automatically
    is_active: bool = True


class PolicyCreate(PolicyIn):
    apply_to_all_customers: bool = False   # also give it to every customer that already exists


class PoliciesIn(BaseModel):
    policy_ids: list[int]
