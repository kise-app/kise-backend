"""Database models owned by the **Identity** context.

Rows, not aggregates. No rules, no validation, no behaviour: every invariant is enforced in
``identity/domain/models/owner.py`` before a mapper hands anything to this class. The domain never
imports this module, and the architecture test proves it.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from kise.platform.database import Base

__all__ = [
    "AdministratorModel",
    "LinkedIdentityModel",
    "OwnerModel",
    "PaymentSettingsModel",
    "PlanModel",
    "PlanSettingsModel",
]


class OwnerModel(Base):
    """One account."""

    __tablename__ = "owners"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(60))
    preferred_calendar: Mapped[str] = mapped_column(String(10))
    preferred_language: Mapped[str] = mapped_column(String(2))
    currency: Mapped[str] = mapped_column(String(3))
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # A plan's code (``plans.code``). Not a foreign key: a plan is removed only once nobody is on
    # it, and an unknown code reads as Basic.
    plan: Mapped[str] = mapped_column(String(20), default="basic", server_default="basic")
    plan_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"OwnerModel(id={self.id}, email={self.email!r})"


class PlanSettingsModel(Base):
    """Plan policy an admin can change. A single row, id 1."""

    __tablename__ = "plan_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trial_days: Mapped[int] = mapped_column(Integer, default=14)


class LinkedIdentityModel(Base):
    """A Google, Apple or Facebook account linked to an Owner."""

    __tablename__ = "linked_identities"
    __table_args__ = (
        # One provider account belongs to one Owner.
        UniqueConstraint("provider", "subject", name="linked_identity_provider_subject"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("owners.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(16))
    subject: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"LinkedIdentityModel(provider={self.provider!r}, owner_id={self.owner_id})"


class AdministratorModel(Base):
    """Someone who can sign in to the admin portal. Not an Owner: no row in ``owners``."""

    __tablename__ = "administrators"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Who added them; NULL for one created at the command line, or once the creator is removed.
    created_by: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("administrators.id", ondelete="SET NULL"), nullable=True
    )
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    credentials_version: Mapped[int] = mapped_column(Integer, default=1)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"AdministratorModel(id={self.id}, email={self.email!r})"


class PaymentSettingsModel(Base):
    """How to pay for Pro, as an admin set it. A single row, id 1."""

    __tablename__ = "payment_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # [{"bank": ..., "holder": ..., "number": ...}, ...]
    accounts: Mapped[list] = mapped_column(JSON, default=list)
    telegram: Mapped[str] = mapped_column(String(32), default="")
    phone: Mapped[str] = mapped_column(String(32), default="")
    email: Mapped[str] = mapped_column(String(254), default="")
    note: Mapped[str] = mapped_column(Text, default="")


class PlanModel(Base):
    """One plan Kise offers. ``receipts`` / ``voice`` NULL is no monthly limit."""

    __tablename__ = "plans"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(40))
    price: Mapped[str] = mapped_column(String(120), default="")
    typed_requests: Mapped[bool] = mapped_column(Boolean, default=False)
    receipts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    voice: Mapped[int | None] = mapped_column(Integer, nullable=True)
    voice_seconds: Mapped[int] = mapped_column(Integer, default=60)
    position: Mapped[int] = mapped_column(Integer, default=0)
    visible: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"PlanModel(code={self.code!r})"
