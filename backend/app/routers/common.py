"""Small helpers shared by the routers."""
import json

from fastapi import HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..sbom_core import build_cyclonedx, build_spdx


def get_or_404(db: Session, model, obj_id: int):
    """Load one row by id, or answer 404 Not Found."""
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(404, f"{model.__tablename__} {obj_id} not found")
    return obj


def consolidated_download(name: str, version: str, components: list, sources: list, fmt: str) -> Response:
    """Build a consolidated SBOM (CycloneDX by default, SPDX if fmt == 'spdx') as a file download."""
    if fmt == "spdx":
        document = build_spdx(name, version, components)
        extension = "spdx"
    else:
        document = build_cyclonedx(name, version, components, sources)
        extension = "cdx"

    file_name = f"{name}-{version}-consolidated.{extension}.json".replace(" ", "_")
    return Response(
        json.dumps(document, indent=2, default=str),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )
