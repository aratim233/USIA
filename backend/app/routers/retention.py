"""
Retention & compliance.

A policy is one regulation or compliance scheme (EU CRA, JC-STAR, an internal
rule ...) with its own retention period. Customers are given the policies of
their market; every product version delivered to them is then kept under all
of those policies.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models as m
from ..auth import current_user, require_admin
from ..db import get_db
from ..presenters import policy_dict, record_dict
from ..schemas import PolicyCreate, PolicyIn
from ..services import applicable_policies, audit, recompute_all
from .common import get_or_404

router = APIRouter(prefix="/api/retention", tags=["retention"])


def all_policies(db: Session) -> list[m.RetentionPolicy]:
    return db.query(m.RetentionPolicy).order_by(m.RetentionPolicy.code).all()


def validate_policy(db: Session, body: PolicyIn, policy_id: int | None = None) -> None:
    """Check the clock option and that code and name are not used by another policy.

    The code is also tidied up: 'jc star' becomes 'JC_STAR'.
    """
    if body.clock_start not in m.CLOCK_STARTS:
        raise HTTPException(422, f"clock_start must be one of {', '.join(m.CLOCK_STARTS)}")

    body.code = body.code.strip().upper().replace(" ", "_")

    for field_name, value in (("code", body.code), ("name", body.name)):
        query = db.query(m.RetentionPolicy).filter(getattr(m.RetentionPolicy, field_name) == value)
        if policy_id is not None:
            query = query.filter(m.RetentionPolicy.id != policy_id)  # ignore the policy being edited
        if query.first():
            raise HTTPException(409, f"A policy with {field_name} '{value}' already exists")


# ---------------------------------------------------------------- policies

@router.get("/policies")
def list_policies(db: Session = Depends(get_db), _user=Depends(current_user)):
    return [policy_dict(db, p) for p in all_policies(db)]


@router.post("/policies", status_code=201)
def create_policy(body: PolicyCreate, db: Session = Depends(get_db), user=Depends(require_admin)):
    validate_policy(db, body)

    data = body.model_dump(exclude={"apply_to_all_customers"})
    policy = m.RetentionPolicy(**data)
    db.add(policy)
    db.flush()

    if body.apply_to_all_customers:
        for customer in db.query(m.Customer).all():
            if policy not in customer.policies:
                customer.policies.append(policy)
        recompute_all(db)

    audit(db, user.username, "CREATE", "retention_policy", policy.id,
          {**data, "applied_to_all_customers": body.apply_to_all_customers})
    db.commit()
    return {"id": policy.id}


@router.put("/policies/{policy_id}")
def update_policy(policy_id: int, body: PolicyIn, db: Session = Depends(get_db), user=Depends(require_admin)):
    policy = get_or_404(db, m.RetentionPolicy, policy_id)
    validate_policy(db, body, policy_id)

    before = policy_dict(db, policy)
    new_values = body.model_dump()
    for field_name, value in new_values.items():
        setattr(policy, field_name, value)
    db.flush()

    # A changed rule changes the dates of everything it applies to.
    versions_checked = recompute_all(db)

    audit(db, user.username, "UPDATE", "retention_policy", policy_id, {
        "before": {k: before[k] for k in new_values},
        "after": new_values,
        "versions_recomputed": versions_checked,
    })
    db.commit()
    return {"ok": True}


@router.delete("/policies/{policy_id}")
def delete_policy(policy_id: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    policy = get_or_404(db, m.RetentionPolicy, policy_id)

    # A policy that customers still depend on can only be switched off, not deleted.
    customers_using = db.query(m.CustomerPolicy).filter_by(policy_id=policy_id).count()
    if customers_using:
        audit(db, user.username, "DELETE_DENIED", "retention_policy", policy_id,
              {"code": policy.code, "customers": customers_using})
        db.commit()
        raise HTTPException(
            409,
            f"{policy.name} applies to {customers_using} customer(s). Remove it from those customers "
            f"or deactivate it instead; deletion refused",
        )

    db.delete(policy)
    audit(db, user.username, "DELETE", "retention_policy", policy_id, {"code": policy.code, "name": policy.name})
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- records

@router.get("")
def retention_list(db: Session = Depends(get_db), _user=Depends(current_user)):
    """All policies, plus one row per product version per regulation that applies to it."""
    records = (
        db.query(m.RetentionRecord)
        .join(m.ProductVersion)
        .join(m.Product)
        .join(m.RetentionPolicy)
        .order_by(m.Product.code, m.ProductVersion.version, m.RetentionPolicy.code)
        .all()
    )

    # Work out "required by which customers" once per product version.
    applicable_cache: dict[int, dict] = {}

    def customers_requiring(record: m.RetentionRecord) -> list[str]:
        version_id = record.product_version_id
        if version_id not in applicable_cache:
            applicable_cache[version_id] = applicable_policies(db, version_id)
        info = applicable_cache[version_id].get(record.policy_id, {})
        return sorted(info.get("customers", []))

    rows = []
    for record in records:
        version = record.product_version
        rows.append({
            "product_version_id": record.product_version_id,
            "label": f"{version.product.code} {version.product.name} v{version.version}",
            "placed_on_market": version.placed_on_market,
            "support_period_end": version.support_period_end,
            **record_dict(record, customers_requiring(record)),
        })

    return {"policies": [policy_dict(db, p) for p in all_policies(db)], "records": rows}


@router.post("/recompute")
def retention_recompute(db: Session = Depends(get_db), user=Depends(require_admin)):
    """Recalculate every retention date now (also happens automatically on start-up)."""
    versions_checked = recompute_all(db)
    audit(db, user.username, "RECOMPUTE", "retention_record", None, {"versions": versions_checked})
    db.commit()
    return {"recomputed": versions_checked}
