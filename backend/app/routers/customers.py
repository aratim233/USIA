"""
Customers, their solutions, and what was delivered to them.

Customer -> Solution -> Solution version (1.0, 2.0 = major) -> Deliveries (1, 2 ... = minor updates)

The solution items are the working list of product versions. Pressing Deliver
freezes that list and its consolidated SBOM as a locked delivery. A minor update
is a changed working list delivered again under the same solution version.

Retention follows the customer, so anything that changes what was delivered,
or the support end agreed for a version, recalculates the retention dates.
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from .. import models as m
from ..auth import current_user, require_admin
from ..db import get_db
from ..presenters import policy_brief
from ..schemas import CustomerIn, CustomerUpdate, DeliveryIn, ItemIn, PoliciesIn, SolutionIn, SolutionVersionIn
from ..services import (NothingToDeliver, attach_default_policies, audit, consolidated_components,
                        create_delivery, customer_label, delivery_intact, next_code, recompute_all, recompute_retention,
                        recompute_solution_retention, solution_version_locked)
from .common import consolidated_download, get_or_404

router = APIRouter(prefix="/api", tags=["customers"])


# ---------------------------------------------------------------- listing

def item_dict(item: m.SolutionItem) -> dict:
    product_version = item.product_version
    product = product_version.product
    return {
        "id": item.id,
        "product_version_id": item.product_version_id,
        "quantity": item.quantity,
        "label": f"{product.code} {product.name} v{product_version.version}",
    }


def delivery_dict(delivery: m.SolutionDelivery) -> dict:
    return {
        "id": delivery.id,
        "number": delivery.number,
        "delivered_on": delivery.delivered_on,
        "note": delivery.note,
        "sha256": delivery.sha256,
        "spdx_sha256": delivery.spdx_sha256,
        "component_count": delivery.component_count,
        "created_by": delivery.created_by,
        "intact": delivery_intact(delivery),   # both locked files unchanged?
        "products": [
            f"{i.product_version.product.code} {i.product_version.product.name} v{i.product_version.version}"
            for i in delivery.items
        ],
    }


def solution_version_dict(sv: m.SolutionVersion) -> dict:
    planned = {item.product_version_id for item in sv.items}
    last = sv.deliveries[-1] if sv.deliveries else None
    delivered = {i.product_version_id for i in last.items} if last else set()
    return {
        "id": sv.id,
        "version": sv.version,
        "delivered_on": sv.delivered_on,
        "support_end": sv.support_end,
        "retention": {
            "retain_until": sv.retain_until,
            "status": sv.retention_status or "NOT DELIVERED",
            "basis": sv.retention_basis,
        },
        "items": [item_dict(item) for item in sv.items],
        "deliveries": [delivery_dict(d) for d in sv.deliveries],
        # The working list differs from the last delivery: a minor update is waiting to be delivered.
        "undelivered_changes": bool(planned) and planned != delivered,
    }


def solution_dict(solution: m.Solution) -> dict:
    return {
        "id": solution.id,
        "name": solution.name,
        "versions": [solution_version_dict(sv) for sv in solution.versions],
    }


def refuse_if_locked(db: Session, user, solution_versions: list[m.SolutionVersion], what: str, obj_id: int):
    """Locked delivery SBOMs under retention must not disappear with their customer or solution."""
    locked = [sv for sv in solution_versions if solution_version_locked(sv)]
    if locked:
        latest = max((sv.retain_until for sv in locked if sv.retain_until), default=None)
        audit(db, user.username, "DELETE_DENIED", what, obj_id,
              {"locked_versions": [sv.version for sv in locked], "retain_until": str(latest)})
        db.commit()
        until = f" until {latest}" if latest else ""
        raise HTTPException(409, f"Delivered SBOMs of this {what.replace('_', ' ')} are locked under "
                                 f"retention{until}; deletion refused")


@router.get("/customers")
def list_customers(db: Session = Depends(get_db), _user=Depends(current_user)):
    customers = db.query(m.Customer).order_by(m.Customer.code)
    return [
        {
            "id": c.id,
            "code": c.code,
            "name": c.name,
            "country": c.country,
            "policies": [policy_brief(p) for p in c.policies],
            "solutions": [solution_dict(s) for s in c.solutions],
        }
        for c in customers
    ]


# ---------------------------------------------------------------- customers

@router.post("/customers", status_code=201)
def create_customer(body: CustomerIn, db: Session = Depends(get_db), user=Depends(require_admin)):
    if db.query(m.Customer).filter_by(name=body.name).first():
        raise HTTPException(409, f"Customer {body.name} already exists")

    customer = m.Customer(code=next_code(db, m.Customer, "C"), **body.model_dump())
    db.add(customer)
    attach_default_policies(db, customer)  # e.g. EU CRA for every new customer
    db.flush()

    audit(db, user.username, "CREATE", "customer", customer.id, {
        "code": customer.code,
        **body.model_dump(),
        "policies": [p.code for p in customer.policies],
    })
    db.commit()
    return {"id": customer.id, "code": customer.code}


@router.put("/customers/{customer_id}/policies")
def set_customer_policies(customer_id: int, body: PoliciesIn, db: Session = Depends(get_db),
                          user=Depends(require_admin)):
    """Choose which regulations apply to a customer."""
    customer = get_or_404(db, m.Customer, customer_id)
    before = sorted(p.code for p in customer.policies)

    unique_ids = list(dict.fromkeys(body.policy_ids))      # drop repeated ids, keep the order
    customer.policies = [get_or_404(db, m.RetentionPolicy, pid) for pid in unique_ids]
    versions_checked = recompute_all(db)

    audit(db, user.username, "UPDATE", "customer_policy", customer_id, {
        "customer": customer_label(customer),
        "before": before,
        "after": sorted(p.code for p in customer.policies),
        "versions_recomputed": versions_checked,
    })
    db.commit()
    return {"ok": True}


@router.put("/customers/{customer_id}")
def update_customer(customer_id: int, body: CustomerUpdate, db: Session = Depends(get_db),
                    user=Depends(require_admin)):
    customer = get_or_404(db, m.Customer, customer_id)

    name_taken = (
        db.query(m.Customer)
        .filter(m.Customer.name == body.name, m.Customer.id != customer_id)
        .first()
    )
    if name_taken:
        raise HTTPException(409, f"Another customer is already called {body.name}")

    before = {"name": customer.name, "country": customer.country}
    customer.name = body.name
    customer.country = body.country

    audit(db, user.username, "UPDATE", "customer", customer_id, {"before": before, "after": body.model_dump()})
    db.commit()
    return {"ok": True}


@router.delete("/customers/{customer_id}")
def delete_customer(customer_id: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    """Removes the customer and its solutions. Refused while any delivered SBOM
    of the customer is under retention. Product SBOMs are never touched."""
    customer = get_or_404(db, m.Customer, customer_id)
    refuse_if_locked(db, user, [sv for s in customer.solutions for sv in s.versions], "customer", customer_id)
    details = {"code": customer.code, "name": customer.name, "solutions": len(customer.solutions)}

    db.delete(customer)
    db.flush()
    recompute_all(db)

    audit(db, user.username, "DELETE", "customer", customer_id, details)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- solutions

@router.post("/customers/{customer_id}/solutions", status_code=201)
def create_solution(customer_id: int, body: SolutionIn, db: Session = Depends(get_db),
                    user=Depends(require_admin)):
    get_or_404(db, m.Customer, customer_id)

    solution = m.Solution(customer_id=customer_id, name=body.name)
    db.add(solution)
    db.flush()

    audit(db, user.username, "CREATE", "solution", solution.id, body.model_dump())
    db.commit()
    return {"id": solution.id}


@router.put("/solutions/{solution_id}")
def update_solution(solution_id: int, body: SolutionIn, db: Session = Depends(get_db),
                    user=Depends(require_admin)):
    solution = get_or_404(db, m.Solution, solution_id)
    old_name = solution.name
    solution.name = body.name

    audit(db, user.username, "UPDATE", "solution", solution_id, {"before": old_name, "after": body.name})
    db.commit()
    return {"ok": True}


@router.delete("/solutions/{solution_id}")
def delete_solution(solution_id: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    solution = get_or_404(db, m.Solution, solution_id)
    refuse_if_locked(db, user, solution.versions, "solution", solution_id)
    details = {"name": solution.name}

    db.delete(solution)
    db.flush()
    recompute_all(db)

    audit(db, user.username, "DELETE", "solution", solution_id, details)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- solution versions

@router.post("/solutions/{solution_id}/versions", status_code=201)
def create_solution_version(solution_id: int, body: SolutionVersionIn, db: Session = Depends(get_db),
                            user=Depends(require_admin)):
    get_or_404(db, m.Solution, solution_id)

    solution_version = m.SolutionVersion(solution_id=solution_id, **body.model_dump())
    db.add(solution_version)
    db.flush()
    db.refresh(solution_version)
    recompute_solution_retention(db, solution_version)

    audit(db, user.username, "CREATE", "solution_version", solution_version.id, body.model_dump(mode="json"))
    db.commit()
    return {"id": solution_version.id}


@router.put("/solution-versions/{solution_version_id}")
def update_solution_version(solution_version_id: int, body: SolutionVersionIn,
                            db: Session = Depends(get_db), user=Depends(require_admin)):
    solution_version = get_or_404(db, m.SolutionVersion, solution_version_id)

    version_taken = (
        db.query(m.SolutionVersion)
        .filter(
            m.SolutionVersion.solution_id == solution_version.solution_id,
            m.SolutionVersion.version == body.version,
            m.SolutionVersion.id != solution_version_id,
        )
        .first()
    )
    if version_taken:
        raise HTTPException(409, f"Version {body.version} already exists for this solution")

    before = {"version": solution_version.version, "support_end": str(solution_version.support_end)}
    solution_version.version = body.version
    solution_version.support_end = body.support_end
    if body.delivered_on is not None:
        solution_version.delivered_on = body.delivered_on

    # A new support end moves the retention of everything delivered in this version.
    delivered_versions = {i.product_version for d in solution_version.deliveries for i in d.items}
    for product_version in delivered_versions:
        recompute_retention(db, product_version)
    recompute_solution_retention(db, solution_version)

    audit(db, user.username, "UPDATE", "solution_version", solution_version_id,
          {"before": before, "after": body.model_dump(mode="json")})
    db.commit()
    return {"ok": True}


@router.delete("/solution-versions/{solution_version_id}")
def delete_solution_version(solution_version_id: int, db: Session = Depends(get_db),
                            user=Depends(require_admin)):
    solution_version = get_or_404(db, m.SolutionVersion, solution_version_id)
    refuse_if_locked(db, user, [solution_version], "solution_version", solution_version_id)
    delivered_versions = [i.product_version for d in solution_version.deliveries for i in d.items]
    details = {"version": solution_version.version}

    db.delete(solution_version)
    db.flush()
    for product_version in delivered_versions:
        recompute_retention(db, product_version)

    audit(db, user.username, "DELETE", "solution_version", solution_version_id, details)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- items (what was delivered)

@router.post("/solution-versions/{solution_version_id}/items", status_code=201)
def add_item(solution_version_id: int, body: ItemIn, db: Session = Depends(get_db),
             user=Depends(require_admin)):
    get_or_404(db, m.SolutionVersion, solution_version_id)
    product_version = get_or_404(db, m.ProductVersion, body.product_version_id)

    already_there = (
        db.query(m.SolutionItem)
        .filter_by(solution_version_id=solution_version_id, product_version_id=body.product_version_id)
        .first()
    )
    if already_there:
        raise HTTPException(409, "That product version is already in this solution version")

    item = m.SolutionItem(solution_version_id=solution_version_id, **body.model_dump())
    db.add(item)
    db.flush()
    recompute_retention(db, product_version)  # it is now delivered to this customer

    audit(db, user.username, "CREATE", "solution_item", item.id, body.model_dump())
    db.commit()
    return {"id": item.id}


@router.delete("/solution-items/{item_id}")
def remove_item(item_id: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    item = get_or_404(db, m.SolutionItem, item_id)
    product_version = item.product_version

    db.delete(item)
    db.flush()
    recompute_retention(db, product_version)

    audit(db, user.username, "DELETE", "solution_item", item_id)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- deliveries (locked)

@router.post("/solution-versions/{solution_version_id}/deliveries", status_code=201)
def deliver(solution_version_id: int, body: DeliveryIn, db: Session = Depends(get_db),
            user=Depends(require_admin)):
    """Deliver the working list: freeze its consolidated SBOM as a locked delivery.

    The first delivery of a version is number 1. Swapping in a newer product
    version and delivering again is a minor update (number 2 ...); the earlier
    deliveries stay locked and the version's retention date is shared.
    """
    solution_version = get_or_404(db, m.SolutionVersion, solution_version_id)
    try:
        delivery = create_delivery(db, solution_version, body.delivered_on, body.note, user.username)
    except NothingToDeliver as error:
        raise HTTPException(400, str(error))
    db.commit()
    return {"id": delivery.id, "number": delivery.number, "sha256": delivery.sha256,
            "components": delivery.component_count}


@router.get("/deliveries/{delivery_id}/sbom")
def delivery_sbom(delivery_id: int, format: str = "cyclonedx", db: Session = Depends(get_db),
                  user=Depends(current_user)):
    """The locked SBOM of one delivery, byte for byte as it was frozen.
    format=cyclonedx (default) or format=spdx."""
    delivery = get_or_404(db, m.SolutionDelivery, delivery_id)
    if format == "spdx":
        if delivery.spdx_content is None:
            raise HTTPException(404, "This delivery has no SPDX copy")
        content, extension = delivery.spdx_content, "spdx"
    else:
        content, extension = delivery.sbom_content, "cdx"
    audit(db, user.username, "DOWNLOAD", "solution_delivery", delivery_id, {"format": format})
    db.commit()

    sv = delivery.solution_version
    customer = sv.solution.customer
    file_name = (f"{customer.code or 'customer'}-{sv.solution.name}-{sv.version}"
                 f"-delivery{delivery.number}.{extension}.json")
    return Response(
        content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{file_name.replace(" ", "_")}"'},
    )


@router.post("/deliveries/{delivery_id}/verify")
def verify_delivery(delivery_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    """Hash both locked files again and compare them with the hashes saved at delivery."""
    delivery = get_or_404(db, m.SolutionDelivery, delivery_id)
    intact = delivery_intact(delivery)
    audit(db, user.username, "INTEGRITY_CHECK", "solution_delivery", delivery_id, {"all_ok": intact})
    db.commit()
    return {"intact": intact, "sha256": delivery.sha256, "spdx_sha256": delivery.spdx_sha256}


# ---------------------------------------------------------------- export

@router.get("/solution-versions/{solution_version_id}/consolidated")
def solution_export(solution_version_id: int, format: str = "cyclonedx", db: Session = Depends(get_db),
                    user=Depends(current_user)):
    """Consolidated SBOM of the current working list (a preview of the next delivery).
    Delivered SBOMs are downloaded from /deliveries/{id}/sbom instead."""
    solution_version = get_or_404(db, m.SolutionVersion, solution_version_id)
    version_ids = [item.product_version_id for item in solution_version.items]
    if not version_ids:
        raise HTTPException(400, "Solution version has no products")

    components, sources = consolidated_components(db, version_ids)
    audit(db, user.username, "EXPORT", "solution_version", solution_version_id,
          {"format": format, "components": len(components)})
    db.commit()

    customer = solution_version.solution.customer
    name = f"{customer.code or ''}-{customer.name}-{solution_version.solution.name}".lstrip("-")
    return consolidated_download(name, solution_version.version, components, sources, format)
