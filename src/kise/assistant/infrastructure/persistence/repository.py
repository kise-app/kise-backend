"""SQLAlchemy repository of the Assistant context: monthly usage counts.

It reads and writes inside the request's Unit of Work and never commits; the application service
commits through the Unit of Work once the model has answered.
"""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.orm import Session

from kise.assistant.application.ports import UsageRepository
from kise.assistant.domain.usage import UsageKind
from kise.assistant.infrastructure.persistence.models import AssistantUsageModel
from kise.shared_kernel.domain.identifiers import OwnerId


class SqlAlchemyUsageRepository(UsageRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def used(self, owner_id: OwnerId, kind: UsageKind, period: str) -> int:
        row = self._session.get(AssistantUsageModel, (owner_id.value, kind.value, period))
        return row.count if row is not None else 0

    def add_one(self, owner_id: OwnerId, kind: UsageKind, period: str) -> None:
        # Incremented in the database rather than read-modify-write, so two requests at once both
        # count. The first request of a month inserts the row instead.
        result = self._session.execute(
            update(AssistantUsageModel)
            .where(
                AssistantUsageModel.owner_id == owner_id.value,
                AssistantUsageModel.kind == kind.value,
                AssistantUsageModel.period == period,
            )
            .values(count=AssistantUsageModel.count + 1)
            .execution_options(synchronize_session="fetch")
        )
        if not result.rowcount:
            self._session.add(
                AssistantUsageModel(
                    owner_id=owner_id.value, kind=kind.value, period=period, count=1
                )
            )
            self._session.flush()
