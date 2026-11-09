"""
Database connection.

DATABASE_URL says where PostgreSQL is. The default matches docker-compose.yml
and the local setup (user, password and database all called 'usia').
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg2://usia:usia@localhost:5432/usia")

# pool_pre_ping checks a pooled connection still works before using it,
# e.g. after PostgreSQL was restarted.
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Parent class of every table in models.py."""


def get_db():
    """One database session per API request, always closed afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
