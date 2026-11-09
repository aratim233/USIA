"""Signing in. The token returned here is sent with every other request."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from .. import models as m
from ..auth import create_token, current_user, verify_password
from ..db import get_db
from ..services import audit

router = APIRouter(prefix="/api/auth", tags=["login"])


@router.post("/login")
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(m.AppUser).filter_by(username=form.username).first()

    # Same message for "no such user" and "wrong password", so the reply
    # doesn't reveal which usernames exist.
    if not user or not verify_password(form.password, user.password_hash):
        raise HTTPException(401, "Incorrect username or password")

    audit(db, user.username, "LOGIN", "app_user", user.id)
    db.commit()

    return {
        "access_token": create_token(user.username, user.role),
        "token_type": "bearer",
        "username": user.username,
        "role": user.role,
    }


@router.get("/me")
def me(user: m.AppUser = Depends(current_user)):
    return {"username": user.username, "role": user.role}
