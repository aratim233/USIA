"""SBOM files: upload, view, download, delete, consolidated export and integrity check."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from .. import models as m
from ..auth import current_user, require_admin
from ..db import get_db
from ..services import (DuplicateSbom, audit, consolidated_components, ingest_sbom, integrity_check,
                        records_for)
from .common import consolidated_download, get_or_404

router = APIRouter(prefix="/api", tags=["sboms"])


@router.post("/versions/{version_id}/sboms", status_code=201)
async def upload_sbom(version_id: int, microservice: str = Form(...), file: UploadFile = File(...),
                      db: Session = Depends(get_db), user=Depends(require_admin)):
    version = get_or_404(db, m.ProductVersion, version_id)
    raw = await file.read()

    try:
        document = ingest_sbom(db, version, microservice, file.filename or "sbom.json", raw, user.username)
    except DuplicateSbom as error:
        raise HTTPException(409, str(error))
    except ValueError as error:          # not JSON, not CycloneDX/SPDX, or a component without a PURL
        raise HTTPException(422, str(error))

    db.commit()
    return {
        "id": document.id,
        "format": document.format,
        "components": document.component_count,
        "sha256": document.sha256,
    }


@router.get("/sboms/{sbom_id}/components")
def sbom_components(sbom_id: int, db: Session = Depends(get_db), _user=Depends(current_user)):
    """Components of one SBOM, as declared in the file and as stored after normalising."""
    document = get_or_404(db, m.SbomDocument, sbom_id)
    return [
        {
            "name": entry.declared_name,
            "version": entry.declared_version,
            "declared_purl": entry.declared_purl,
            "canonical_purl": entry.component.purl,
            "licenses": entry.licenses,
            "supplier": entry.supplier,
        }
        for entry in document.entries
    ]


@router.get("/sboms/{sbom_id}/original")
def sbom_original(sbom_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    """The original file, byte for byte as it was uploaded."""
    document = get_or_404(db, m.SbomDocument, sbom_id)
    audit(db, user.username, "DOWNLOAD", "sbom_document", sbom_id)
    db.commit()
    return Response(
        document.raw_content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{document.file_name}"'},
    )


@router.delete("/sboms/{sbom_id}")
def delete_sbom(sbom_id: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    document = get_or_404(db, m.SbomDocument, sbom_id)

    # The deletion lock: refuse while any regulation still requires the file.
    # The refusal itself is logged too.
    still_required = [
        record for record in records_for(db, document.product_version_id)
        if record.status in ("ACTIVE", "EXPIRING")
    ]
    if still_required:
        latest = max(still_required, key=lambda r: r.retain_until)
        audit(db, user.username, "DELETE_DENIED", "sbom_document", sbom_id, {
            "retain_until": str(latest.retain_until),
            "policies": {r.policy.code: str(r.retain_until) for r in still_required},
        })
        db.commit()
        reasons = ", ".join(f"{r.policy.name} until {r.retain_until}" for r in still_required)
        raise HTTPException(409, f"SBOM is under retention ({reasons}); deletion refused")

    db.delete(document)
    audit(db, user.username, "DELETE", "sbom_document", sbom_id, {"file": document.file_name})
    db.commit()
    return {"ok": True}


@router.get("/versions/{version_id}/components")
def version_components(version_id: int, db: Session = Depends(get_db), _user=Depends(current_user)):
    """Merged component list for one product version, shown on its page."""
    components, sources = consolidated_components(db, [version_id])
    version = get_or_404(db, m.ProductVersion, version_id)
    declared_total = sum(d.component_count for d in version.sboms)
    return {
        "components": components,
        "sources": sources,
        "raw_count": declared_total,
        "unique_count": len(components),
    }


@router.get("/versions/{version_id}/consolidated")
def version_export(version_id: int, format: str = "cyclonedx", db: Session = Depends(get_db),
                   user=Depends(current_user)):
    version = get_or_404(db, m.ProductVersion, version_id)
    components, sources = consolidated_components(db, [version_id])

    audit(db, user.username, "EXPORT", "product_version", version_id,
          {"format": format, "components": len(components)})
    db.commit()

    name = f"{version.product.code}-{version.product.name}"
    return consolidated_download(name, version.version, components, sources, format)


@router.post("/versions/{version_id}/integrity")
def version_integrity(version_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    version = get_or_404(db, m.ProductVersion, version_id)
    result = integrity_check(db, version)
    audit(db, user.username, "INTEGRITY_CHECK", "product_version", version_id, {"all_ok": result["all_ok"]})
    db.commit()
    return result
