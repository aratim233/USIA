"""Read the audit log, newest entries first."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models as m
from ..auth import current_user
from ..db import get_db

router = APIRouter(prefix="/api", tags=["audit"])


@router.get("/audit")
def audit_log(limit: int = 200, db: Session = Depends(get_db), _user=Depends(current_user)):
    entries = db.query(m.AuditLog).order_by(m.AuditLog.id.desc()).limit(limit)
    return [
        {
            "id": entry.id,
            "at": entry.at,
            "username": entry.username,
            "action": entry.action,
            "entity": entry.entity,
            "entity_id": entry.entity_id,
            "details": entry.details,
        }
        for entry in entries
    ]
