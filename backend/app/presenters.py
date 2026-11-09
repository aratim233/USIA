"""
How database rows are turned into the JSON the screens receive.

Several routers show the same things (a product version, a policy, a retention
record), so the shapes are defined once here.
"""
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import models as m
from .services import applicable_policies, customer_label, deliveries, records_for, retention_summary


def record_dict(record: m.RetentionRecord, required_by: list[str] | None = None) -> dict:
    """One retention record: one product version under one regulation."""
    return {
        "policy_id": record.policy_id,
        "policy_code": record.policy.code,
        "policy_name": record.policy.name,
        "legal_basis": record.policy.legal_basis,
        "retain_until": record.retain_until,
        "status": record.status,
        "basis": record.basis,
        "integrity_ok": record.integrity_ok,
        "required_by": required_by or [],
    }


def version_dict(db: Session, version: m.ProductVersion) -> dict:
    """A product version with its customers and its retention under every regulation."""
    records = records_for(db, version.id)
    applicable = applicable_policies(db, version.id)

    def customers_requiring(record: m.RetentionRecord) -> list[str]:
        info = applicable.get(record.policy_id, {})
        return sorted(info.get("customers", []))

    records_by_code = sorted(records, key=lambda r: r.policy.code or "")

    return {
        "id": version.id,
        "version": version.version,
        "placed_on_market": version.placed_on_market,
        "support_period_end": version.support_period_end,
        "sbom_count": len(version.sboms),
        "customers": sorted({customer_label(c) for c, _date, _sv in deliveries(db, version.id)}),
        "retention": retention_summary(records),
        "retention_records": [record_dict(r, customers_requiring(r)) for r in records_by_code],
    }


def policy_brief(policy: m.RetentionPolicy) -> dict:
    """Short form, used for the regulation labels next to a customer."""
    return {"id": policy.id, "code": policy.code, "name": policy.name, "is_active": policy.is_active}


def policy_dict(db: Session, policy: m.RetentionPolicy) -> dict:
    """Full policy, including how many customers it applies to."""
    customer_count = (
        db.query(func.count())
        .select_from(m.CustomerPolicy)
        .filter(m.CustomerPolicy.policy_id == policy.id)
        .scalar()
    )
    return {
        "id": policy.id,
        "code": policy.code,
        "name": policy.name,
        "legal_basis": policy.legal_basis,
        "description": policy.description,
        "min_years": policy.min_years,
        "clock_start": policy.clock_start,
        "use_support_period_if_longer": policy.use_support_period_if_longer,
        "warn_days_before_expiry": policy.warn_days_before_expiry,
        "apply_by_default": policy.apply_by_default,
        "is_active": policy.is_active,
        "customer_count": customer_count,
    }
