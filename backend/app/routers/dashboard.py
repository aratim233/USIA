"""Numbers for the dashboard tiles."""
from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models as m
from ..auth import current_user
from ..db import get_db
from ..services import records_for, retention_summary

router = APIRouter(prefix="/api", tags=["dashboard"])


def count(db: Session, column) -> int:
    return db.query(func.count(column)).scalar()


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), _user=Depends(current_user)):
    # How many product versions are in each retention status.
    # A version not yet delivered to any customer has no retention at all.
    versions_by_status: dict[str, int] = {}
    for version in db.query(m.ProductVersion).all():
        summary = retention_summary(records_for(db, version.id))
        status = summary["status"] if summary else "NOT DELIVERED"
        versions_by_status[status] = versions_by_status.get(status, 0) + 1

    total_components = count(db, m.Component.id)
    with_real_purl = (
        db.query(func.count(m.Component.id))
        .filter(m.Component.has_declared_purl.is_(True))
        .scalar()
    )
    purl_coverage = round(100 * with_real_purl / total_components, 1) if total_components else 0

    storage_bytes = db.query(func.coalesce(func.sum(m.SbomDocument.size_bytes), 0)).scalar()
    active_policies = (
        db.query(func.count(m.RetentionPolicy.id))
        .filter(m.RetentionPolicy.is_active.is_(True))
        .scalar()
    )
    formats = dict(
        db.query(m.SbomDocument.format, func.count())
        .group_by(m.SbomDocument.format)
        .all()
    )

    return {
        "products": count(db, m.Product.id),
        "product_versions": count(db, m.ProductVersion.id),
        "sboms": count(db, m.SbomDocument.id),
        "components": total_components,
        "purl_coverage_pct": purl_coverage,
        "storage_bytes": storage_bytes,
        "customers": count(db, m.Customer.id),
        "solutions": count(db, m.Solution.id),
        "deliveries": count(db, m.SolutionDelivery.id),
        "retention": versions_by_status,
        "policies": active_policies,
        "formats": formats,
    }
