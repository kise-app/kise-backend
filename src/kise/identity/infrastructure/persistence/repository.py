"""SQLAlchemy repositories of the Identity context: Owners, and the accounts linked to them."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from kise.identity.domain.errors import OwnerNotFound
from kise.identity.domain.models import EmailAddress, LinkedIdentity, Owner, SocialProvider
from kise.identity.infrastructure.persistence.mappers import LinkedIdentityMapper, OwnerMapper
from kise.identity.infrastructure.persistence.models import LinkedIdentityModel, OwnerModel
from kise.shared_kernel.domain.identifiers import OwnerId
from kise.shared_kernel.infrastructure.repository import TrackingRepository


class SqlAlchemyOwnerRepository(TrackingRepository[Owner]):
    mapper = OwnerMapper

    def __init__(self, session: Session) -> None:
        super().__init__(session)

    @classmethod
    def model_type(cls) -> type[OwnerModel]:
        return OwnerModel

    def add(self, owner: Owner) -> None:
        self._insert(owner)

    def get(self, owner_id: OwnerId) -> Owner:
        cached = self._cached(owner_id)
        if cached is not None:
            return cached
        model = self._session.get(OwnerModel, owner_id.value)
        if model is None:
            raise OwnerNotFound("No such account", owner_id=str(owner_id))
        return self._rehydrate(model)

    def find_by_email(self, email: EmailAddress) -> Owner | None:
        model = self._session.scalars(
            select(OwnerModel).where(OwnerModel.email == str(email))
        ).one_or_none()
        return self._rehydrate(model) if model is not None else None

    def email_exists(self, email: EmailAddress) -> bool:
        found = self._session.scalar(
            select(func.count()).select_from(OwnerModel).where(OwnerModel.email == str(email))
        )
        return bool(found)


class SqlAlchemyLinkedIdentityRepository(TrackingRepository[LinkedIdentity]):
    mapper = LinkedIdentityMapper

    def __init__(self, session: Session) -> None:
        super().__init__(session)

    @classmethod
    def model_type(cls) -> type[LinkedIdentityModel]:
        return LinkedIdentityModel

    def add(self, identity: LinkedIdentity) -> None:
        self._insert(identity)

    def find(self, provider: SocialProvider, subject: str) -> LinkedIdentity | None:
        model = self._session.scalars(
            select(LinkedIdentityModel).where(
                LinkedIdentityModel.provider == provider.value,
                LinkedIdentityModel.subject == subject,
            )
        ).one_or_none()
        return self._rehydrate(model) if model is not None else None

    def list_for_owner(self, owner_id: OwnerId) -> list[LinkedIdentity]:
        statement = select(LinkedIdentityModel).where(
            LinkedIdentityModel.owner_id == owner_id.value
        )
        return self._rehydrate_all(self._session.scalars(statement).all())
