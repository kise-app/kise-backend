"""Database models owned by the **Assistant** context.

Rows, not rules: what counts as a month and what an allowance allows live in
``assistant/domain/usage.py``.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from kise.platform.database import Base


class AssistantUsageModel(Base):
    """How many receipt scans or voice notes an owner used in one month."""

    __tablename__ = "assistant_usage"

    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("owners.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(8), primary_key=True)
    #: ``YYYY-MM``
    period: Mapped[str] = mapped_column(String(7), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
