"""
Database preparation, run once each time the backend starts.

1. Create any missing tables.
2. Upgrade databases made by earlier versions of the app (new columns etc.).
3. Make sure the two demo users and the EU CRA policy exist.
4. Record deliveries for solution versions made before deliveries existed.
5. Recalculate every retention date, so statuses such as "expiring soon"
   are correct for today.
"""
from sqlalchemy import text

from . import models as m
from .auth import hash_password
from .db import Base, SessionLocal, engine
from .sbom_core import sha256_hex
from .services import (attach_default_policies, consolidated_components, create_delivery, delivery_documents,
                       ensure_default_policy, next_code, recompute_all)

# create_all() only creates tables that don't exist yet; it never changes an
# existing table. These statements bring an older database up to date.
# Each one is written so it is safe to run again on every start.
UPGRADE_STATEMENTS = [
    # retention policies became one row per regulation
    "ALTER TABLE retention_policy ADD COLUMN IF NOT EXISTS code VARCHAR(30)",
    "ALTER TABLE retention_policy ADD COLUMN IF NOT EXISTS description TEXT",
    "ALTER TABLE retention_policy ADD COLUMN IF NOT EXISTS clock_start VARCHAR(30) DEFAULT 'placed_on_market'",
    "ALTER TABLE retention_policy ADD COLUMN IF NOT EXISTS apply_by_default BOOLEAN DEFAULT FALSE",
    "UPDATE retention_policy SET clock_start = 'placed_on_market' WHERE clock_start IS NULL",
    "UPDATE retention_policy SET apply_by_default = FALSE WHERE apply_by_default IS NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_retention_policy_code ON retention_policy (code)",

    # a product version can now have one retention record per regulation
    "ALTER TABLE retention_record DROP CONSTRAINT IF EXISTS retention_record_product_version_id_key",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_retention_version_policy_idx "
    "ON retention_record (product_version_id, policy_id)",

    # an earlier draft linked regulations to products; they now belong to customers
    "DROP TABLE IF EXISTS product_policy",

    # customers got a readable ID (C01, C02 ...)
    "ALTER TABLE customer ADD COLUMN IF NOT EXISTS code VARCHAR(20)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_customer_code ON customer (code)",

    # solution versions got a customer support end and their own retention date
    "ALTER TABLE solution_version ADD COLUMN IF NOT EXISTS support_end DATE",
    "ALTER TABLE solution_version ADD COLUMN IF NOT EXISTS retain_until DATE",
    "ALTER TABLE solution_version ADD COLUMN IF NOT EXISTS retention_status VARCHAR(20)",
    "ALTER TABLE solution_version ADD COLUMN IF NOT EXISTS retention_basis VARCHAR(300)",

    # deliveries are locked in SPDX as well as CycloneDX
    "ALTER TABLE solution_delivery ADD COLUMN IF NOT EXISTS spdx_content BYTEA",
    "ALTER TABLE solution_delivery ADD COLUMN IF NOT EXISTS spdx_sha256 VARCHAR(64)",
]


def upgrade_schema() -> None:
    # The statements use PostgreSQL syntax (IF NOT EXISTS ...).
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        for statement in UPGRADE_STATEMENTS:
            conn.execute(text(statement))


def prepare_database() -> None:
    Base.metadata.create_all(engine)
    upgrade_schema()

    with SessionLocal() as db:
        # Demo users, only on an empty database.
        if not db.query(m.AppUser).first():
            db.add(m.AppUser(username="admin", password_hash=hash_password("admin123"), role="admin"))
            db.add(m.AppUser(username="viewer", password_hash=hash_password("viewer123"), role="viewer"))

        ensure_default_policy(db)

        # Customers created before IDs existed get one now, in the order they were added.
        customers_without_id = (
            db.query(m.Customer).filter(m.Customer.code.is_(None)).order_by(m.Customer.id).all()
        )
        for customer in customers_without_id:
            customer.code = next_code(db, m.Customer, "C")
            db.flush()

        # Customers created before regulations were per customer get the default ones.
        if db.query(m.CustomerPolicy).count() == 0:
            for customer in db.query(m.Customer).all():
                attach_default_policies(db, customer)

        # Solution versions from before deliveries existed: their product list was
        # what the customer received, so record it as delivery 1 (and lock it).
        for sv in db.query(m.SolutionVersion).all():
            if sv.items and not sv.deliveries:
                create_delivery(db, sv, sv.delivered_on, "Recorded from the earlier solution list", "system")

        # Deliveries locked before the SPDX copy existed get one, built from the
        # same delivered product versions. The CycloneDX file is left untouched.
        for delivery in db.query(m.SolutionDelivery).filter(m.SolutionDelivery.spdx_content.is_(None)).all():
            sv = delivery.solution_version
            customer = sv.solution.customer
            components, sources = consolidated_components(db, [i.product_version_id for i in delivery.items])
            title = f"{customer.code or ''} {customer.name} - {sv.solution.name}".strip()
            _, spdx = delivery_documents(title, f"{sv.version} delivery {delivery.number}", components, sources)
            delivery.spdx_content = spdx
            delivery.spdx_sha256 = sha256_hex(spdx)

        db.flush()
        recompute_all(db)
        db.commit()
