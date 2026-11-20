"""SQLAlchemy repositories of the Identity context: Owners, the accounts linked to them, plan
settings, and the admin portal's administrators."""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from kise.identity.domain.errors import AdministratorNotFound, OwnerNotFound
from kise.identity.domain.models import (
    Administrator,
    EmailAddress,
    LinkedIdentity,
    Owner,
    PaymentAccount,
    PaymentInstructions,
    PlanPolicy,
    SocialProvider,
)
from kise.identity.infrastructure.persistence.mappers import (
    AdministratorMapper,
    LinkedIdentityMapper,
    OwnerMapper,
)
from kise.identity.infrastructure.persistence.models import (
    AdministratorModel,
    LinkedIdentityModel,
    OwnerModel,
    PaymentSettingsModel,
    PlanSettingsModel,
)
from kise.shared_kernel.domain.identifiers import AdministratorId, OwnerId
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

    def search(self, text: str = "", *, limit: int = 50, offset: int = 0) -> list[Owner]:
        """Newest first; ``text`` matches the email or the name, case-insensitively."""
        statement = self._filtered(select(OwnerModel), text)
        statement = statement.order_by(OwnerModel.registered_at.desc(), OwnerModel.id)
        return self._rehydrate_all(
            self._session.scalars(statement.limit(limit).offset(offset)).all()
        )

    def count(self, text: str = "") -> int:
        statement = self._filtered(select(func.count()).select_from(OwnerModel), text)
        return int(self._session.scalar(statement) or 0)

    def count_by_plan(self) -> dict[str, int]:
        rows = self._session.execute(
            select(OwnerModel.plan, func.count()).group_by(OwnerModel.plan)
        ).all()
        return {plan: int(count) for plan, count in rows}

    @staticmethod
    def _filtered(statement, text: str):
        cleaned = (text or "").strip()
        if not cleaned:
            return statement
        escaped = cleaned.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        return statement.where(
            or_(
                OwnerModel.email.ilike(pattern, escape="\\"),
                OwnerModel.display_name.ilike(pattern, escape="\\"),
            )
        )

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


class SqlAlchemyPlanSettingsRepository:
    """Where the ``PlanPolicy`` is kept: a single row. A setting read and written whole, not an
    aggregate with an identity, so there is nothing to track."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self) -> PlanPolicy:
        row = self._session.get(PlanSettingsModel, 1)
        return PlanPolicy(trial_days=row.trial_days) if row is not None else PlanPolicy()

    def save(self, policy: PlanPolicy) -> None:
        row = self._session.get(PlanSettingsModel, 1)
        if row is None:
            self._session.add(PlanSettingsModel(id=1, trial_days=policy.trial_days))
        else:
            row.trial_days = policy.trial_days


class SqlAlchemyAdministratorRepository(TrackingRepository[Administrator]):
    mapper = AdministratorMapper

    def __init__(self, session: Session) -> None:
        super().__init__(session)

    @classmethod
    def model_type(cls) -> type[AdministratorModel]:
        return AdministratorModel

    def add(self, admin: Administrator) -> None:
        self._insert(admin)

    def remove(self, admin: Administrator) -> None:
        self._delete(admin)

    def get(self, administrator_id: AdministratorId) -> Administrator:
        cached = self._cached(administrator_id)
        if cached is not None:
            return cached
        model = self._session.get(AdministratorModel, administrator_id.value)
        if model is None:
            raise AdministratorNotFound("No such admin", administrator_id=str(administrator_id))
        return self._rehydrate(model)

    def find_by_email(self, email: EmailAddress) -> Administrator | None:
        model = self._session.scalars(
            select(AdministratorModel).where(AdministratorModel.email == str(email))
        ).one_or_none()
        return self._rehydrate(model) if model is not None else None

    def list(self) -> list[Administrator]:
        statement = select(AdministratorModel).order_by(
            AdministratorModel.created_at, AdministratorModel.email
        )
        return self._rehydrate_all(self._session.scalars(statement).all())

    def count(self) -> int:
        return int(
            self._session.scalar(select(func.count()).select_from(AdministratorModel)) or 0
        )


class SqlAlchemyPaymentSettingsRepository:
    """Where the ``PaymentInstructions`` are kept: a single row, read and written whole."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self) -> PaymentInstructions:
        row = self._session.get(PaymentSettingsModel, 1)
        if row is None:
            return PaymentInstructions()
        return PaymentInstructions(
            accounts=tuple(
                PaymentAccount(bank=a["bank"], holder=a["holder"], number=a["number"])
                for a in (row.accounts or [])
            ),
            basic_price=row.basic_price or "",
            pro_price=row.pro_price or "",
            telegram=row.telegram or "",
            phone=row.phone or "",
            email=row.email or "",
            note=row.note or "",
        )

    def save(self, instructions: PaymentInstructions) -> None:
        row = self._session.get(PaymentSettingsModel, 1)
        if row is None:
            row = PaymentSettingsModel(id=1)
            self._session.add(row)
        row.accounts = [
            {"bank": a.bank, "holder": a.holder, "number": a.number}
            for a in instructions.accounts
        ]
        row.basic_price = instructions.basic_price
        row.pro_price = instructions.pro_price
        row.telegram = instructions.telegram
        row.phone = instructions.phone
        row.email = instructions.email
        row.note = instructions.note
