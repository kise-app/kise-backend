"""Owner <-> OwnerModel, LinkedIdentity <-> LinkedIdentityModel, Administrator <-> its row."""

from __future__ import annotations

from datetime import UTC, datetime

from kise.identity.domain.models import (
    Administrator,
    EmailAddress,
    LinkedIdentity,
    Owner,
    PasswordHash,
    Preferences,
    Subscription,
)
from kise.identity.infrastructure.persistence.models import (
    AdministratorModel,
    LinkedIdentityModel,
    OwnerModel,
)
from kise.shared_kernel.domain.calendar.calendar_kind import CalendarKind, Language
from kise.shared_kernel.domain.identifiers import AdministratorId, LinkedIdentityId, OwnerId


def _aware(value: datetime | None) -> datetime | None:
    """SQLite hands timestamps back without a zone; Kise stores them in UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class OwnerMapper:
    """Translates the Owner aggregate to and from its row."""

    @staticmethod
    def to_model(owner: Owner) -> OwnerModel:
        return OwnerModel(
            id=owner.id.value,
            email=str(owner.email),
            password_hash=owner.password_hash.value,
            display_name=owner.display_name,
            preferred_calendar=owner.preferences.calendar.value,
            preferred_language=owner.preferences.language.value,
            currency=owner.preferences.currency,
            registered_at=owner.registered_at,
            plan=owner.subscription.plan.value,
            plan_expires_at=owner.subscription.expires_at,
        )

    @staticmethod
    def to_domain(entity: OwnerModel) -> Owner:
        # The constructor, not ``Owner.register``: rehydrating must not re-raise OwnerRegistered.
        return Owner(
            OwnerId(entity.id),
            email=EmailAddress(entity.email),
            password_hash=PasswordHash(entity.password_hash),
            display_name=entity.display_name,
            preferences=Preferences(
                calendar=CalendarKind.parse(entity.preferred_calendar),
                language=Language.parse(entity.preferred_language),
                currency=entity.currency,
            ),
            registered_at=entity.registered_at,
            subscription=Subscription(entity.plan or "basic", _aware(entity.plan_expires_at)),
        )

    @staticmethod
    def update_model(entity: OwnerModel, owner: Owner) -> None:
        entity.plan = owner.subscription.plan.value
        entity.plan_expires_at = owner.subscription.expires_at
        entity.email = str(owner.email)
        entity.password_hash = owner.password_hash.value
        entity.display_name = owner.display_name
        entity.preferred_calendar = owner.preferences.calendar.value
        entity.preferred_language = owner.preferences.language.value
        entity.currency = owner.preferences.currency


class LinkedIdentityMapper:
    @staticmethod
    def to_model(identity: LinkedIdentity) -> LinkedIdentityModel:
        return LinkedIdentityModel(
            id=identity.id.value,
            owner_id=identity.owner_id.value,
            provider=identity.provider.value,
            subject=identity.subject,
            email=identity.email,
            linked_at=identity.linked_at,
        )

    @staticmethod
    def to_domain(entity: LinkedIdentityModel) -> LinkedIdentity:
        return LinkedIdentity(
            LinkedIdentityId(entity.id),
            owner_id=OwnerId(entity.owner_id),
            provider=entity.provider,
            subject=entity.subject,
            email=entity.email,
            linked_at=entity.linked_at,
        )

    @staticmethod
    def update_model(entity: LinkedIdentityModel, identity: LinkedIdentity) -> None:
        entity.email = identity.email


class AdministratorMapper:
    @staticmethod
    def to_model(admin: Administrator) -> AdministratorModel:
        return AdministratorModel(
            id=admin.id.value,
            email=str(admin.email),
            password_hash=admin.password_hash.value,
            created_at=admin.created_at,
            created_by=admin.created_by.value if admin.created_by else None,
            password_changed_at=admin.password_changed_at,
            credentials_version=admin.credentials_version,
        )

    @staticmethod
    def to_domain(entity: AdministratorModel) -> Administrator:
        return Administrator(
            AdministratorId(entity.id),
            email=EmailAddress(entity.email),
            password_hash=PasswordHash(entity.password_hash),
            created_at=_aware(entity.created_at),
            created_by=AdministratorId(entity.created_by) if entity.created_by else None,
            password_changed_at=_aware(entity.password_changed_at),
            credentials_version=entity.credentials_version,
        )

    @staticmethod
    def update_model(entity: AdministratorModel, admin: Administrator) -> None:
        entity.password_hash = admin.password_hash.value
        entity.password_changed_at = admin.password_changed_at
        entity.credentials_version = admin.credentials_version
