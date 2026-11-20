"""What the Identity service returns.

``OwnerProfile`` is what the settings screen shows. ``AuthenticatedOwner`` pairs it with the token
that proves it, so registering and signing in are each a single round trip.

What the service *accepts* lives in ``commands.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kise.identity.application.ports import AccessToken
from kise.identity.domain.models import Administrator, LinkedIdentity, Owner, Plan, PlanPolicy
from kise.shared_kernel.domain.calendar.calendar_kind import CalendarKind, Language
from kise.shared_kernel.domain.identifiers import AdministratorId, OwnerId


@dataclass(frozen=True, slots=True)
class OwnerProfile:
    """What the app shows on the settings screen."""

    owner_id: OwnerId
    email: str
    display_name: str
    calendar: CalendarKind
    language: Language
    currency: str
    registered_at: datetime
    #: The plan on record, and the one in force now (an ended trial is Basic).
    plan: Plan = Plan.BASIC
    effective_plan: Plan = Plan.BASIC
    plan_expires_at: datetime | None = None
    #: Whole days left on a trial or a dated Pro plan; None when it does not end.
    plan_days_left: int | None = None
    #: May use the assistant: voice notes, receipt scans, questions.
    ai_enabled: bool = False

    @classmethod
    def of(cls, owner: Owner, now: datetime) -> OwnerProfile:
        """An Owner as the app and the admin portal see it, with the plan in force at ``now``."""
        subscription = owner.subscription
        return cls(
            owner_id=owner.id,
            email=str(owner.email),
            display_name=owner.display_name,
            calendar=owner.preferences.calendar,
            language=owner.preferences.language,
            currency=owner.preferences.currency,
            registered_at=owner.registered_at,
            plan=subscription.plan,
            effective_plan=subscription.effective(now),
            plan_expires_at=subscription.expires_at,
            plan_days_left=subscription.days_left(now),
            ai_enabled=subscription.has_ai(now),
        )


@dataclass(frozen=True, slots=True)
class AuthenticatedOwner:
    """A profile plus the token that proves it."""

    profile: OwnerProfile
    token: AccessToken
    #: True when this sign-in made a new account (social sign-in can do either).
    created: bool = False


@dataclass(frozen=True, slots=True)
class AccountView:
    """One account as the admin portal lists it: the profile and how its owner signs in."""

    profile: OwnerProfile
    sign_in_methods: tuple[str, ...]

    @classmethod
    def of(cls, owner: Owner, identities: list[LinkedIdentity], now: datetime) -> AccountView:
        methods = ("password", *sorted(identity.provider.value for identity in identities))
        return cls(profile=OwnerProfile.of(owner, now), sign_in_methods=methods)


@dataclass(frozen=True, slots=True)
class AccountPage:
    accounts: tuple[AccountView, ...]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class PlanOverview:
    """The portal's dashboard: how many accounts are on each plan, and the policy in force."""

    total: int
    by_plan: dict[str, int]
    policy: PlanPolicy


@dataclass(frozen=True, slots=True)
class AdministratorView:
    """An admin as the portal shows one. Never the password hash."""

    administrator_id: AdministratorId
    email: str
    created_at: datetime
    #: Who added them; None for one created at the command line (or whose creator was removed).
    created_by_email: str | None
    password_changed_at: datetime
    #: A portal session is only good for the version it was issued for.
    credentials_version: int

    @classmethod
    def of(cls, admin: Administrator, created_by_email: str | None = None) -> AdministratorView:
        return cls(
            administrator_id=admin.id,
            email=str(admin.email),
            created_at=admin.created_at,
            created_by_email=created_by_email,
            password_changed_at=admin.password_changed_at,
            credentials_version=admin.credentials_version,
        )


@dataclass(frozen=True, slots=True)
class PlanCard:
    """One plan as the portal's Plans page shows it."""

    plan: Plan
    #: Free text for Basic and Pro; always empty for Trial.
    price: str
    #: Only for Trial: how long new accounts get it.
    trial_days: int | None
    ai_enabled: bool
    #: Accounts on this plan on record.
    accounts: int
    #: Receipt scans and voice notes a month; None is no monthly limit.
    receipts: int | None = None
    voice: int | None = None
