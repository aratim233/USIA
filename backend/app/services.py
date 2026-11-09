"""
Service layer.

The API routes stay thin: they check who is calling, then call a function in
here that does the actual work. Every change is also written to the audit log
from here (the "logging component" in the architecture).

Sections:
    audit logging
    business IDs (P01, C01 ...)
    SBOM upload
    consolidation (merging SBOMs)
    deliveries (locked SBOM snapshot of what a customer received)
    retention (which regulations apply, and until when)
    integrity check
    impact search
"""
import json
from datetime import date, datetime

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from . import models as m
from .sbom_core import (build_cyclonedx, build_spdx, compute_retention, parse_sbom, require_purls, sha256_hex,
                        support_end_only)


# ==========================================================================
# Audit logging
# ==========================================================================

def audit(db: Session, username: str | None, action: str, entity: str,
          entity_id: int | None = None, details: dict | None = None) -> None:
    """Record who did what. Saved in the same transaction as the change itself,
    so a change and its log entry are either both stored or both rolled back."""
    db.add(m.AuditLog(
        username=username,
        action=action,
        entity=entity,
        entity_id=entity_id,
        details=details,
    ))


# ==========================================================================
# Business IDs
# ==========================================================================

def next_code(db: Session, model, prefix: str) -> str:
    """Next readable ID for products (P01, P02 ...) or customers (C01, C02 ...).

    Takes the highest number already used with that prefix and adds one.
    After 99 it simply carries on with three digits (P100).
    """
    used_numbers = []
    for (code,) in db.query(model.code).filter(model.code.like(f"{prefix}%")).all():
        number_part = (code or "")[len(prefix):]
        if number_part.isdigit():
            used_numbers.append(int(number_part))

    next_number = max(used_numbers) + 1 if used_numbers else 1
    return f"{prefix}{next_number:02d}"


def customer_label(customer: m.Customer) -> str:
    """'C01 Northern Grid Utility' - used wherever a customer is shown in a list."""
    if customer.code:
        return f"{customer.code} {customer.name}"
    return customer.name


# ==========================================================================
# SBOM upload
# ==========================================================================

class DuplicateSbom(Exception):
    """The exact same file (same SHA-256) is already stored for this product version."""


def ingest_sbom(db: Session, pv: m.ProductVersion, microservice: str, file_name: str,
                raw: bytes, username: str) -> m.SbomDocument:
    """Store an uploaded SBOM as evidence and index its components.

    1. Hash the file and refuse an exact duplicate.
    2. Parse it, and check every component declares a PURL (raises ValueError
       if it isn't CycloneDX or SPDX JSON, or a PURL is missing).
    3. Keep the original bytes untouched, together with the hash.
    4. For every component: reuse the shared COMPONENT row with the same
       canonical PURL, or create it, and record what this SBOM declared.
    5. Write the audit entry and refresh the retention dates.
    """
    digest = sha256_hex(raw)
    already_stored = db.query(m.SbomDocument).filter_by(product_version_id=pv.id, sha256=digest).first()
    if already_stored:
        raise DuplicateSbom("This exact SBOM is already stored for this product version")

    parsed = parse_sbom(raw)
    require_purls(parsed)  # every component must declare its PURL; nothing is invented

    document = m.SbomDocument(
        product_version_id=pv.id,
        microservice=microservice,
        file_name=file_name,
        format=parsed.format,
        spec_version=parsed.spec_version,
        generator_tool=parsed.tool,
        serial_or_namespace=parsed.serial,
        sha256=digest,
        raw_content=raw,
        size_bytes=len(raw),
        component_count=len(parsed.components),
        uploaded_by=username,
    )
    db.add(document)
    db.flush()  # gives the document its id

    # The same library can appear several times in one file, so remember the
    # rows we have already looked up during this upload.
    seen: dict[str, m.Component] = {}

    for parsed_comp in parsed.components:
        key = parsed_comp.canonical_purl
        component = seen.get(key) or db.query(m.Component).filter_by(purl=key).first()

        if component is None:
            component = m.Component(
                purl=key,
                name=parsed_comp.name,
                version=parsed_comp.version,
                has_declared_purl=parsed_comp.has_purl,
            )
            db.add(component)
            db.flush()
        seen[key] = component

        # Keep the values exactly as this SBOM declared them.
        db.add(m.SbomComponent(
            sbom_id=document.id,
            component_id=component.id,
            declared_name=parsed_comp.name,
            declared_version=parsed_comp.version,
            declared_purl=parsed_comp.purl,
            licenses=parsed_comp.licenses,
            supplier=parsed_comp.supplier,
            component_type=parsed_comp.component_type,
            hashes=parsed_comp.hashes,
        ))

    audit(db, username, "UPLOAD", "sbom_document", document.id, {
        "file": file_name,
        "format": f"{parsed.format} {parsed.spec_version}",
        "components": len(parsed.components),
        "sha256": digest,
    })
    recompute_retention(db, pv)
    return document


# ==========================================================================
# Consolidation
# ==========================================================================

def consolidated_components(db: Session, product_version_ids: list[int]) -> tuple[list[dict], list[dict]]:
    """Merge every SBOM of the given product versions into one component list.

    Components are matched on their canonical PURL, so a library used by five
    microservices appears once, with all five listed under "found_in".
    Returns (components, source_documents).
    """
    documents = (
        db.query(m.SbomDocument)
        .filter(m.SbomDocument.product_version_id.in_(product_version_ids))
        .all()
    )

    merged: dict[str, dict] = {}
    for document in documents:
        for entry in document.entries:
            purl = entry.component.purl

            if purl not in merged:
                merged[purl] = {
                    "purl": purl,
                    "name": entry.component.name,
                    "version": entry.component.version,
                    "licenses": [],
                    "supplier": entry.supplier,
                    "type": entry.component_type,
                    "found_in": [],
                }
            item = merged[purl]

            for licence in entry.licenses or []:
                if licence not in item["licenses"]:
                    item["licenses"].append(licence)
            if not item["supplier"]:
                item["supplier"] = entry.supplier
            item["found_in"].append(document.microservice)

    sources = [
        {"file_name": d.file_name, "sha256": d.sha256, "microservice": d.microservice}
        for d in documents
    ]
    components = sorted(merged.values(), key=lambda c: c["purl"])
    return components, sources


# ==========================================================================
# Deliveries
# ==========================================================================

class NothingToDeliver(Exception):
    """The solution version has no product versions in its working list."""


def delivery_documents(title: str, version: str, components: list[dict],
                       sources: list[dict]) -> tuple[bytes, bytes]:
    """The consolidated SBOM of one delivery as file bytes: (CycloneDX, SPDX)."""
    cyclonedx = build_cyclonedx(title, version, components, sources)
    spdx = build_spdx(title, version, components)
    return (json.dumps(cyclonedx, indent=2, default=str).encode(),
            json.dumps(spdx, indent=2, default=str).encode())


def delivery_intact(delivery: m.SolutionDelivery) -> bool:
    """True when both locked files still match the hashes saved at delivery."""
    if sha256_hex(delivery.sbom_content) != delivery.sha256:
        return False
    if delivery.spdx_content is not None and sha256_hex(delivery.spdx_content) != delivery.spdx_sha256:
        return False
    return True


def create_delivery(db: Session, sv: m.SolutionVersion, delivered_on: date | None, note: str | None,
                    username: str) -> m.SolutionDelivery:
    """Deliver the current working list of a solution version and lock its SBOM.

    The consolidated SBOM of everything delivered is built once in both formats
    (CycloneDX and SPDX), stored as bytes with a SHA-256 each, and never changed
    afterwards. The first delivery of a
    version is number 1; a minor update (a newer product version swapped in)
    becomes number 2, and so on. Earlier deliveries stay as they were.
    """
    if not sv.items:
        raise NothingToDeliver("Add at least one product version before delivering")

    number = (max((d.number for d in sv.deliveries), default=0)) + 1
    delivered_on = delivered_on or date.today()
    customer = sv.solution.customer

    components, sources = consolidated_components(db, [item.product_version_id for item in sv.items])
    title = f"{customer.code or ''} {customer.name} - {sv.solution.name}".strip()
    cyclonedx, spdx = delivery_documents(title, f"{sv.version} delivery {number}", components, sources)

    delivery = m.SolutionDelivery(
        solution_version_id=sv.id,
        number=number,
        delivered_on=delivered_on,
        note=note,
        sbom_content=cyclonedx,
        sha256=sha256_hex(cyclonedx),
        spdx_content=spdx,
        spdx_sha256=sha256_hex(spdx),
        component_count=len(components),
        created_by=username,
    )
    for item in sv.items:
        delivery.items.append(m.DeliveryItem(product_version_id=item.product_version_id, quantity=item.quantity))
    sv.deliveries.append(delivery)
    db.flush()

    audit(db, username, "DELIVER", "solution_delivery", delivery.id, {
        "solution": f"{customer_label(customer)} / {sv.solution.name} v{sv.version}",
        "delivery": number,
        "products": [f"{i.product_version.product.code} v{i.product_version.version}" for i in sv.items],
        "components": len(components),
        "sha256": delivery.sha256,
        "spdx_sha256": delivery.spdx_sha256,
    })

    # What was delivered is now under retention.
    for item in delivery.items:
        recompute_retention(db, item.product_version)
    recompute_solution_retention(db, sv)
    return delivery


# ==========================================================================
# Retention
#
# Retention follows the customer. Each customer falls under the regulations of
# its market (EU CRA, JC-STAR ...). A product version delivered to a customer
# must be kept under all of that customer's regulations, and at least until
# the support end of the solution version it was delivered in. Each pair
# (product version, regulation) gets one RETENTION_RECORD; each solution
# version stores its own date for its locked delivery SBOMs.
# ==========================================================================

EU_CRA = dict(
    code="EU_CRA",
    name="EU Cyber Resilience Act",
    legal_basis="Regulation (EU) 2024/2847, Article 13(13)",
    description="Keep the technical documentation, including the SBOM, for at least 10 years "
                "after placing on the market, or for the support period if longer.",
    min_years=10,
    clock_start="placed_on_market",
    use_support_period_if_longer=True,
    warn_days_before_expiry=365,
    apply_by_default=True,
    is_active=True,
)


def ensure_default_policy(db: Session) -> None:
    """On a new database, create the EU CRA policy.

    Databases created by the first version of the app had one CRA policy row
    without a code; give it the EU_CRA code so it keeps working.
    """
    if db.query(m.RetentionPolicy).count() == 0:
        db.add(m.RetentionPolicy(**EU_CRA))
        db.flush()
        return

    for policy in db.query(m.RetentionPolicy).filter(m.RetentionPolicy.code.is_(None)).all():
        is_cra = policy.name.upper().startswith("EU CRA") or "2024/2847" in (policy.legal_basis or "")
        if is_cra:
            policy.code = "EU_CRA"
            policy.apply_by_default = True
            policy.clock_start = policy.clock_start or "placed_on_market"
        else:
            policy.code = f"POLICY_{policy.id}"
    db.flush()


def attach_default_policies(db: Session, customer: m.Customer) -> None:
    """Give a new customer every active policy marked 'apply to new customers'."""
    defaults = db.query(m.RetentionPolicy).filter_by(apply_by_default=True, is_active=True).all()
    for policy in defaults:
        if policy not in customer.policies:
            customer.policies.append(policy)


def deliveries(db: Session, pv_id: int) -> list[tuple[m.Customer, date | None, m.SolutionVersion]]:
    """Every delivery of this product version: (customer, delivery date, solution version).

    Only what was actually delivered counts, so a version replaced by a minor
    update stays under retention for the deliveries it was part of.
    """
    rows = (
        db.query(m.Customer, m.SolutionDelivery.delivered_on, m.SolutionVersion)
        .join(m.Solution, m.Solution.customer_id == m.Customer.id)
        .join(m.SolutionVersion, m.SolutionVersion.solution_id == m.Solution.id)
        .join(m.SolutionDelivery, m.SolutionDelivery.solution_version_id == m.SolutionVersion.id)
        .join(m.DeliveryItem, m.DeliveryItem.delivery_id == m.SolutionDelivery.id)
        .filter(m.DeliveryItem.product_version_id == pv_id)
        .all()
    )
    return [(customer, delivered_on, solution_version) for customer, delivered_on, solution_version in rows]


def applicable_policies(db: Session, pv_id: int) -> dict[int, dict]:
    """Which regulations apply to one product version, and because of whom.

    Returns {policy_id: {"policy", "customers" (labels), "delivered_on" (latest),
    "support_end" (latest customer support end)}}.
    """
    result: dict[int, dict] = {}

    for customer, delivered_on, solution_version in deliveries(db, pv_id):
        for policy in customer.policies:
            if not policy.is_active:
                continue

            if policy.id not in result:
                result[policy.id] = {"policy": policy, "customers": set(), "delivered_on": None,
                                     "support_end": None}
            entry = result[policy.id]
            entry["customers"].add(customer_label(customer))

            # For "clock starts at delivery" we use the most recent delivery.
            if delivered_on and (entry["delivered_on"] is None or delivered_on > entry["delivered_on"]):
                entry["delivered_on"] = delivered_on

            # The SBOM must also be kept until the latest customer support end.
            support_end = solution_version.support_end
            if support_end and (entry["support_end"] is None or support_end > entry["support_end"]):
                entry["support_end"] = support_end

    return result


def recompute_retention(db: Session, pv: m.ProductVersion, today: date | None = None) -> list[m.RetentionRecord]:
    """Bring the retention records of one product version up to date.

    Creates a record for every regulation that now applies, updates the dates
    of existing ones, and removes records for regulations that no longer apply
    (for example after a customer was removed).
    """
    db.flush()  # make sure changes made just before are visible to the queries
    applicable = applicable_policies(db, pv.id)

    existing = {
        record.policy_id: record
        for record in db.query(m.RetentionRecord).filter_by(product_version_id=pv.id).all()
    }
    for policy_id, record in existing.items():
        if policy_id not in applicable:
            db.delete(record)

    updated = []
    for policy_id, info in applicable.items():
        policy = info["policy"]
        result = compute_retention(
            placed_on_market=pv.placed_on_market,
            support_period_end=pv.support_period_end,
            min_years=policy.min_years,
            today=today,
            warn_days=policy.warn_days_before_expiry,
            clock_start=policy.clock_start or "placed_on_market",
            use_support_period_if_longer=policy.use_support_period_if_longer,
            delivered_on=info["delivered_on"],
            customer_support_end=info["support_end"],
        )
        required_by = ", ".join(sorted(info["customers"]))
        basis = f"{result['basis']} (required by {required_by})"[:300]  # column holds 300 characters

        record = existing.get(policy_id)
        if record is None:
            record = m.RetentionRecord(product_version_id=pv.id, policy_id=policy_id,
                                       status=result["status"], basis=basis)
            db.add(record)

        record.retain_until = result["retain_until"]
        record.status = result["status"]
        record.basis = basis
        record.policy = policy
        updated.append(record)

    db.flush()
    return updated


def recompute_solution_retention(db: Session, sv: m.SolutionVersion, today: date | None = None) -> None:
    """Retention of one solution version: how long its locked delivery SBOMs must be kept.

    The later of the customer's support end for this version and every regulation
    of the customer, applied to every product version ever delivered in it.
    Minor updates add deliveries but share this one date.
    """
    if not sv.deliveries:
        sv.retain_until, sv.retention_status, sv.retention_basis = None, "NOT DELIVERED", "Not delivered yet"
        return

    customer = sv.solution.customer
    policies = [p for p in customer.policies if p.is_active]

    results = []
    for delivery in sv.deliveries:
        for item in delivery.items:
            pv = item.product_version
            for policy in policies:
                result = compute_retention(
                    placed_on_market=pv.placed_on_market,
                    support_period_end=pv.support_period_end,
                    min_years=policy.min_years,
                    today=today,
                    warn_days=policy.warn_days_before_expiry,
                    clock_start=policy.clock_start or "placed_on_market",
                    use_support_period_if_longer=policy.use_support_period_if_longer,
                    delivered_on=delivery.delivered_on,
                    customer_support_end=sv.support_end,
                )
                result["basis"] = f"{policy.name}: {result['basis']}"
                results.append(result)

    if not policies:
        results.append(support_end_only(sv.support_end, today))

    dated = [r for r in results if r["retain_until"]]
    binding = max(dated, key=lambda r: r["retain_until"]) if dated else results[0]
    sv.retain_until = binding["retain_until"]
    sv.retention_status = binding["status"]
    sv.retention_basis = binding["basis"][:300]


def recompute_all(db: Session) -> int:
    """Refresh retention for every product version and every solution version.
    Returns how many product versions were checked."""
    versions = db.query(m.ProductVersion).all()
    for version in versions:
        recompute_retention(db, version)
    for solution_version in db.query(m.SolutionVersion).all():
        recompute_solution_retention(db, solution_version)
    db.flush()
    return len(versions)


def solution_version_locked(sv: m.SolutionVersion) -> bool:
    """A solution version with a delivery still under retention cannot be deleted."""
    return bool(sv.deliveries) and sv.retention_status in ("ACTIVE", "EXPIRING", "PENDING")


def records_for(db: Session, pv_id: int) -> list[m.RetentionRecord]:
    return db.query(m.RetentionRecord).filter_by(product_version_id=pv_id).all()


def retention_summary(records: list[m.RetentionRecord]) -> dict | None:
    """One line per product version: the binding date is the latest one across all regulations.

    Deletion is locked while any of the regulations is still running.
    Returns None when no regulation applies (the version was not delivered yet).
    """
    if not records:
        return None

    dated = [r for r in records if r.retain_until]
    if dated:
        binding = max(dated, key=lambda r: r.retain_until)
        status = binding.status
    else:
        binding = records[0]
        status = "PENDING"

    locked = any(r.status in ("ACTIVE", "EXPIRING") for r in records)

    checked = [r for r in records if r.last_integrity_check]
    latest_check = max(checked, key=lambda r: r.last_integrity_check) if checked else None

    return {
        "retain_until": binding.retain_until,
        "status": status,
        "basis": f"{binding.policy.name}: {binding.basis}" if binding.policy else binding.basis,
        "binding_policy": binding.policy.code if binding.policy else None,
        "locked": locked,
        "integrity_ok": latest_check.integrity_ok if latest_check else None,
        "last_integrity_check": latest_check.last_integrity_check if latest_check else None,
    }


# ==========================================================================
# Integrity check
# ==========================================================================

def integrity_check(db: Session, pv: m.ProductVersion) -> dict:
    """Hash every stored original again and compare with the hash saved at upload.

    Any difference means the stored evidence was changed after upload.
    """
    results = []
    for document in pv.sboms:
        unchanged = sha256_hex(document.raw_content) == document.sha256
        results.append({"sbom_id": document.id, "file_name": document.file_name, "ok": unchanged})

    all_ok = all(r["ok"] for r in results)

    checked_at = datetime.utcnow()
    for record in recompute_retention(db, pv):
        record.last_integrity_check = checked_at
        record.integrity_ok = all_ok

    return {"all_ok": all_ok, "documents": results}


# ==========================================================================
# Impact search
# ==========================================================================

def impact_search(db: Session, q: str) -> list[dict]:
    """'A vulnerability was found in X - who is affected?'

    Finds components whose name or PURL contains the search text, then lists
    every product version and every customer solution that contains them.
    """
    pattern = f"%{q.lower()}%"
    matches = (
        db.query(m.Component)
        .filter(or_(
            func.lower(m.Component.name).like(pattern),
            func.lower(m.Component.purl).like(pattern),
        ))
        .limit(50)
        .all()
    )

    results = []
    for component in matches:
        rows = (
            db.query(m.ProductVersion, m.SbomDocument)
            .join(m.SbomDocument, m.SbomDocument.product_version_id == m.ProductVersion.id)
            .join(m.SbomComponent, m.SbomComponent.sbom_id == m.SbomDocument.id)
            .filter(m.SbomComponent.component_id == component.id)
            .all()
        )
        version_ids = {pv.id for pv, _ in rows}
        products = sorted({
            f"{pv.product.code} {pv.product.name} v{pv.version} ({document.microservice})"
            for pv, document in rows
        })

        solution_versions = []
        if version_ids:
            solution_versions = (
                db.query(m.SolutionVersion)
                .join(m.SolutionItem)
                .filter(m.SolutionItem.product_version_id.in_(version_ids))
                .all()
            )
        solutions = sorted({
            f"{customer_label(sv.solution.customer)} / {sv.solution.name} v{sv.version}"
            for sv in solution_versions
        })

        results.append({
            "purl": component.purl,
            "name": component.name,
            "version": component.version,
            "products": products,
            "solutions": solutions,
        })
    return results
