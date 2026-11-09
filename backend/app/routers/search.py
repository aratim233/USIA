"""Impact search: which products and customers contain a given component?"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..services import impact_search

router = APIRouter(prefix="/api", tags=["search"])


@router.get("/search")
def search(q: str, db: Session = Depends(get_db), _user=Depends(current_user)):
    text = q.strip()
    if len(text) < 2:
        raise HTTPException(400, "Query must be at least 2 characters")
    return impact_search(db, text)
