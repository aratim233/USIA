"""Products and their versions."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models as m
from ..auth import current_user, require_admin
from ..db import get_db
from ..presenters import version_dict
from ..schemas import ProductIn, VersionIn
from ..services import audit, next_code, recompute_retention
from .common import get_or_404

router = APIRouter(prefix="/api", tags=["products"])


@router.get("/products")
def list_products(db: Session = Depends(get_db), _user=Depends(current_user)):
    products = db.query(m.Product).order_by(m.Product.code).all()
    return [
        {
            "id": p.id,
            "code": p.code,
            "name": p.name,
            "vendor": p.vendor,
            "description": p.description,
            "versions": [version_dict(db, v) for v in p.versions],
        }
        for p in products
    ]


@router.post("/products", status_code=201)
def create_product(body: ProductIn, db: Session = Depends(get_db), user=Depends(require_admin)):
    # The screen never sends a code, so the next free P-number is used.
    # Scripts (seed.py) may send one so products match their SBOM folders.
    code = (body.code or "").strip().upper() or next_code(db, m.Product, "P")
    if db.query(m.Product).filter_by(code=code).first():
        raise HTTPException(409, f"Product code {code} already exists")

    product = m.Product(**{**body.model_dump(), "code": code})
    db.add(product)
    db.flush()

    audit(db, user.username, "CREATE", "product", product.id, {**body.model_dump(), "code": code})
    db.commit()
    return {"id": product.id, "code": code}


@router.delete("/products/{product_id}")
def delete_product(product_id: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    product = get_or_404(db, m.Product, product_id)

    # Stored SBOMs are evidence: a product that has any can't be removed.
    if any(version.sboms for version in product.versions):
        raise HTTPException(409, "Product has stored SBOMs under retention; delete is not allowed")

    db.delete(product)
    audit(db, user.username, "DELETE", "product", product_id, {"code": product.code})
    db.commit()
    return {"ok": True}


@router.post("/products/{product_id}/versions", status_code=201)
def create_version(product_id: int, body: VersionIn, db: Session = Depends(get_db),
                   user=Depends(require_admin)):
    get_or_404(db, m.Product, product_id)

    duplicate = db.query(m.ProductVersion).filter_by(product_id=product_id, version=body.version).first()
    if duplicate:
        raise HTTPException(409, f"Version {body.version} already exists for this product")

    version = m.ProductVersion(product_id=product_id, **body.model_dump())
    db.add(version)
    db.flush()
    db.refresh(version)
    recompute_retention(db, version)

    audit(db, user.username, "CREATE", "product_version", version.id, body.model_dump(mode="json"))
    db.commit()
    return {"id": version.id}


@router.get("/product-versions")
def all_versions(db: Session = Depends(get_db), _user=Depends(current_user)):
    """Short list for drop-downs, e.g. 'P01 Grid Gateway v1.0.0'."""
    versions = (
        db.query(m.ProductVersion)
        .join(m.Product)
        .order_by(m.Product.code, m.ProductVersion.version)
    )
    return [{"id": v.id, "label": f"{v.product.code} {v.product.name} v{v.version}"} for v in versions]


@router.get("/versions/{version_id}")
def get_version(version_id: int, db: Session = Depends(get_db), _user=Depends(current_user)):
    version = get_or_404(db, m.ProductVersion, version_id)

    sboms = [
        {
            "id": d.id,
            "microservice": d.microservice,
            "file_name": d.file_name,
            "format": d.format,
            "spec_version": d.spec_version,
            "generator_tool": d.generator_tool,
            "sha256": d.sha256,
            "size_bytes": d.size_bytes,
            "component_count": d.component_count,
            "uploaded_by": d.uploaded_by,
            "uploaded_at": d.uploaded_at,
        }
        for d in version.sboms
    ]
    product = {"id": version.product.id, "code": version.product.code, "name": version.product.name}

    return {**version_dict(db, version), "product": product, "sboms": sboms}


@router.patch("/versions/{version_id}")
def update_version(version_id: int, body: VersionIn, db: Session = Depends(get_db),
                   user=Depends(require_admin)):
    version = get_or_404(db, m.ProductVersion, version_id)
    before = {
        "placed_on_market": str(version.placed_on_market),
        "support_period_end": str(version.support_period_end),
    }

    version.version = body.version
    version.placed_on_market = body.placed_on_market
    version.support_period_end = body.support_period_end
    recompute_retention(db, version)  # new dates mean new retention dates

    audit(db, user.username, "UPDATE", "product_version", version.id,
          {"before": before, "after": body.model_dump(mode="json")})
    db.commit()
    return {"ok": True}
