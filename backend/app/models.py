"""
Database tables: 14 domain tables in 4 zones, plus 2 support tables.

    Product zone    PRODUCT, PRODUCT_VERSION
    Customer zone   CUSTOMER, SOLUTION, SOLUTION_VERSION, SOLUTION_ITEM,
                    SOLUTION_DELIVERY (locked SBOM snapshot per delivery), DELIVERY_ITEM
    SBOM zone       SBOM_DOCUMENT, COMPONENT, SBOM_COMPONENT
    Retention zone  RETENTION_POLICY  one row per regulation (EU CRA, JC-STAR ...)
                    CUSTOMER_POLICY   which regulations apply to which customer
                    RETENTION_RECORD  one row per delivered product version per regulation
    Support         APP_USER, AUDIT_LOG

Retention follows the customer: a product version is kept under every
regulation of every customer it was delivered to, and at least until the
support end of the solution version it was delivered in.
"""
from datetime import date, datetime

from sqlalchemy import (JSON, Boolean, Date, DateTime, ForeignKey, Integer, LargeBinary,
                        String, Text, UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


# ----------------------------------------------------------- product zone
class Product(Base):
    """Something we ship, e.g. P01 Grid Gateway."""
    __tablename__ = "product"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)          # P01, P02 ... generated
    name: Mapped[str] = mapped_column(String(200))
    vendor: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    versions: Mapped[list["ProductVersion"]] = relationship(back_populates="product",
                                                            cascade="all, delete-orphan")


class ProductVersion(Base):
    """A released version. Its market and support dates drive the retention clock."""
    __tablename__ = "product_version"
    __table_args__ = (UniqueConstraint("product_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"))
    version: Mapped[str] = mapped_column(String(50))
    placed_on_market: Mapped[date | None] = mapped_column(Date)       # CRA clock start
    support_period_end: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    product: Mapped[Product] = relationship(back_populates="versions")
    sboms: Mapped[list["SbomDocument"]] = relationship(back_populates="product_version",
                                                       cascade="all, delete-orphan")


# ----------------------------------------------------------- customer zone
class Customer(Base):
    """A customer, e.g. C01 Northern Grid Utility, with the regulations of its market."""
    __tablename__ = "customer"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str | None] = mapped_column(String(20), unique=True)   # C01, C02 ... generated
    name: Mapped[str] = mapped_column(String(200), unique=True)
    country: Mapped[str | None] = mapped_column(String(100))
    solutions: Mapped[list["Solution"]] = relationship(back_populates="customer",
                                                       cascade="all, delete-orphan")
    policies: Mapped[list["RetentionPolicy"]] = relationship(secondary="customer_policy",
                                                             order_by="RetentionPolicy.code")


class Solution(Base):
    """A system delivered to a customer, made up of several products."""
    __tablename__ = "solution"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    customer: Mapped[Customer] = relationship(back_populates="solutions")
    versions: Mapped[list["SolutionVersion"]] = relationship(back_populates="solution",
                                                             cascade="all, delete-orphan")


class SolutionVersion(Base):
    """A major version of a solution (1.0, 2.0 ...).

    Minor updates do not create a new solution version; they are new deliveries
    of the same version. The support end agreed with the customer is recorded
    here, so every delivery of this version shares the same retention date.
    """
    __tablename__ = "solution_version"
    __table_args__ = (UniqueConstraint("solution_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    solution_id: Mapped[int] = mapped_column(ForeignKey("solution.id", ondelete="CASCADE"))
    version: Mapped[str] = mapped_column(String(50))
    delivered_on: Mapped[date | None] = mapped_column(Date)       # kept from earlier versions of the app
    support_end: Mapped[date | None] = mapped_column(Date)        # support agreed with the customer
    retain_until: Mapped[date | None] = mapped_column(Date)       # computed: later of support end and regulations
    retention_status: Mapped[str | None] = mapped_column(String(20))
    retention_basis: Mapped[str | None] = mapped_column(String(300))
    solution: Mapped[Solution] = relationship(back_populates="versions")
    items: Mapped[list["SolutionItem"]] = relationship(back_populates="solution_version",
                                                       cascade="all, delete-orphan")
    deliveries: Mapped[list["SolutionDelivery"]] = relationship(back_populates="solution_version",
                                                                order_by="SolutionDelivery.number",
                                                                cascade="all, delete-orphan")


class SolutionItem(Base):
    """Junction: the product versions currently planned for a solution version.

    This is the working list. What the customer actually received is recorded
    by SOLUTION_DELIVERY / DELIVERY_ITEM when the admin presses Deliver.
    """
    __tablename__ = "solution_item"
    __table_args__ = (UniqueConstraint("solution_version_id", "product_version_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    solution_version_id: Mapped[int] = mapped_column(ForeignKey("solution_version.id", ondelete="CASCADE"))
    product_version_id: Mapped[int] = mapped_column(ForeignKey("product_version.id", ondelete="RESTRICT"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    solution_version: Mapped[SolutionVersion] = relationship(back_populates="items")
    product_version: Mapped[ProductVersion] = relationship()


class SolutionDelivery(Base):
    """One delivery of a solution version: 1.0 delivery 1, 1.0 delivery 2 (a minor update) ...

    At delivery the consolidated SBOM of everything delivered is frozen here,
    once as CycloneDX and once as SPDX, each with its own SHA-256. There is no endpoint to change or delete a delivery: it
    is locked evidence of what the customer received.
    """
    __tablename__ = "solution_delivery"
    __table_args__ = (UniqueConstraint("solution_version_id", "number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    solution_version_id: Mapped[int] = mapped_column(ForeignKey("solution_version.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)                 # 1, 2, 3 ... within the solution version
    delivered_on: Mapped[date] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(String(500))       # e.g. "Minor update: P02 1.27 -> 1.28"
    sbom_content: Mapped[bytes] = mapped_column(LargeBinary)     # frozen consolidated CycloneDX 1.5 SBOM
    sha256: Mapped[str] = mapped_column(String(64))
    spdx_content: Mapped[bytes | None] = mapped_column(LargeBinary)  # the same delivery frozen as SPDX 2.3
    spdx_sha256: Mapped[str | None] = mapped_column(String(64))
    component_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    solution_version: Mapped[SolutionVersion] = relationship(back_populates="deliveries")
    items: Mapped[list["DeliveryItem"]] = relationship(back_populates="delivery", cascade="all, delete-orphan")


class DeliveryItem(Base):
    """Junction: exactly which product versions went out in one delivery."""
    __tablename__ = "delivery_item"
    __table_args__ = (UniqueConstraint("delivery_id", "product_version_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    delivery_id: Mapped[int] = mapped_column(ForeignKey("solution_delivery.id", ondelete="CASCADE"))
    product_version_id: Mapped[int] = mapped_column(ForeignKey("product_version.id", ondelete="RESTRICT"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    delivery: Mapped[SolutionDelivery] = relationship(back_populates="items")
    product_version: Mapped[ProductVersion] = relationship()


# ----------------------------------------------------------- SBOM zone
class SbomDocument(Base):
    """Original SBOM preserved byte-for-byte with its SHA-256 (evidentiary record)."""
    __tablename__ = "sbom_document"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_version_id: Mapped[int] = mapped_column(ForeignKey("product_version.id", ondelete="CASCADE"))
    microservice: Mapped[str] = mapped_column(String(200))
    file_name: Mapped[str] = mapped_column(String(300))
    format: Mapped[str] = mapped_column(String(20))
    spec_version: Mapped[str] = mapped_column(String(20))
    generator_tool: Mapped[str | None] = mapped_column(String(200))
    serial_or_namespace: Mapped[str | None] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    raw_content: Mapped[bytes] = mapped_column(LargeBinary)
    size_bytes: Mapped[int] = mapped_column(Integer)
    component_count: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_by: Mapped[str | None] = mapped_column(String(100))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    product_version: Mapped[ProductVersion] = relationship(back_populates="sboms")
    entries: Mapped[list["SbomComponent"]] = relationship(back_populates="sbom",
                                                          cascade="all, delete-orphan")


class Component(Base):
    """Deduplicated canonical component, keyed on normalised PURL."""
    __tablename__ = "component"
    id: Mapped[int] = mapped_column(primary_key=True)
    purl: Mapped[str] = mapped_column(String(1000), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(300), index=True)
    version: Mapped[str | None] = mapped_column(String(100))
    has_declared_purl: Mapped[bool] = mapped_column(Boolean, default=True)


class SbomComponent(Base):
    """Junction keeping the values *as declared* in each SBOM (fidelity)."""
    __tablename__ = "sbom_component"
    id: Mapped[int] = mapped_column(primary_key=True)
    sbom_id: Mapped[int] = mapped_column(ForeignKey("sbom_document.id", ondelete="CASCADE"), index=True)
    component_id: Mapped[int] = mapped_column(ForeignKey("component.id"), index=True)
    declared_name: Mapped[str] = mapped_column(String(300))
    declared_version: Mapped[str | None] = mapped_column(String(100))
    declared_purl: Mapped[str | None] = mapped_column(String(1000))
    licenses: Mapped[list | None] = mapped_column(JSON)
    supplier: Mapped[str | None] = mapped_column(String(300))
    component_type: Mapped[str | None] = mapped_column(String(50))
    hashes: Mapped[dict | None] = mapped_column(JSON)
    sbom: Mapped[SbomDocument] = relationship(back_populates="entries")
    component: Mapped[Component] = relationship()


# ----------------------------------------------------------- retention zone
CLOCK_STARTS = ("placed_on_market", "delivered_to_customer", "support_period_end")


class RetentionPolicy(Base):
    """One retention rule per regulation / compliance scheme (EU CRA, EU MDR, JC-STAR, internal ...).
    retain_until = clock start + min_years, extended to the support period end if that is later
    and use_support_period_if_longer is set."""
    __tablename__ = "retention_policy"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str | None] = mapped_column(String(30), unique=True)        # EU_CRA, JC_STAR ...
    name: Mapped[str] = mapped_column(String(200), unique=True)
    legal_basis: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    min_years: Mapped[int] = mapped_column(Integer, default=10)
    clock_start: Mapped[str] = mapped_column(String(30), default="placed_on_market")
    use_support_period_if_longer: Mapped[bool] = mapped_column(Boolean, default=True)
    warn_days_before_expiry: Mapped[int] = mapped_column(Integer, default=365)
    apply_by_default: Mapped[bool] = mapped_column(Boolean, default=False)  # attached to new customers
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class CustomerPolicy(Base):
    """Junction: which regulations a customer falls under (e.g. an EU customer -> EU CRA,
    a Japanese customer -> JC-STAR)."""
    __tablename__ = "customer_policy"
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id", ondelete="CASCADE"), primary_key=True)
    policy_id: Mapped[int] = mapped_column(ForeignKey("retention_policy.id", ondelete="CASCADE"),
                                           primary_key=True)


class RetentionRecord(Base):
    """Computed retention for one delivered product version under one regulation."""
    __tablename__ = "retention_record"
    __table_args__ = (UniqueConstraint("product_version_id", "policy_id", name="uq_retention_version_policy"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    product_version_id: Mapped[int] = mapped_column(ForeignKey("product_version.id", ondelete="CASCADE"),
                                                    index=True)
    policy_id: Mapped[int] = mapped_column(ForeignKey("retention_policy.id", ondelete="CASCADE"))
    retain_until: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20))          # PENDING/ACTIVE/EXPIRING/EXPIRED
    basis: Mapped[str] = mapped_column(String(300))
    last_integrity_check: Mapped[datetime | None] = mapped_column(DateTime)
    integrity_ok: Mapped[bool | None] = mapped_column(Boolean)
    computed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    product_version: Mapped[ProductVersion] = relationship()
    policy: Mapped[RetentionPolicy] = relationship()


# ----------------------------------------------------------- support
class AppUser(Base):
    """Someone who can sign in. Only the bcrypt hash of the password is stored."""
    __tablename__ = "app_user"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20), default="admin")   # admin / viewer


class AuditLog(Base):
    """Logging component: every change made through the service layer."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    username: Mapped[str | None] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(50))       # CREATE / UPDATE / DELETE / UPLOAD / EXPORT ...
    entity: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict | None] = mapped_column(JSON)
