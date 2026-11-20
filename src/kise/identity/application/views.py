"""What the Identity service returns.

``OwnerProfile`` is what the settings screen shows. ``AuthenticatedOwner`` pairs it with the token
that proves it, so registering and signing in are each a single round trip.

What the service *accepts* lives in ``commands.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kise.identity.application.ports import AccessToken
from kise.identity.domain.models import (
    BASIC,
    Administrator,
    LinkedIdentity,
    Owner,
    PlanCode,
    PlanDefinition,
    PlanPolicy,
)
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
    #: The plan on record, and the one in force now (a plan past its end is Basic).
    plan: PlanCode = BASIC
    effective_plan: PlanCode = BASIC
    #: The name of the plan in force, as an admin wrote it.
    plan_name: str = "Basic"
    plan_expires_at: datetime | None = None
    #: Whole days left on a time-limited plan; None when it does not end.
    plan_days_left: int | None = None
    #: Any part of the assistant: typed requests, receipt scans or voice notes.
    ai_enabled: bool = False
    #: Typed requests to the assistant (text and typed questions).
    typed_requests: bool = False
    #: Receipt scans and voice notes a month (None: no monthly limit), and the longest voice note.
    receipts: int | None = 0
    voice: int | None = 0
    voice_seconds: int = 60

    @classmethod
    def of(cls, owner: Owner, now: datetime, plan: PlanDefinition | None) -> OwnerProfile:
        """An Owner as the app and the admin portal see it, with the plan in force at ``now``.

        ``plan`` is that plan's definition; None (it was removed, which the rules prevent) reads
        as a plan with nothing of the assistant.
        """
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
            plan_name=plan.name if plan else str(subscription.effective(now)).title(),
            plan_expires_at=subscription.expires_at,
            plan_days_left=subscription.days_left(now),
            ai_enabled=plan.includes_assistant if plan else False,
            typed_requests=plan.typed_requests if plan else False,
            receipts=plan.allowance.receipts if plan else 0,
            voice=plan.allowance.voice if plan else 0,
            voice_seconds=plan.allowance.voice_seconds if plan else 60,
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
    def of(
        cls,
        owner: Owner,
        identities: list[LinkedIdentity],
        now: datetime,
        plan: PlanDefinition | None,
    ) -> AccountView:
        methods = ("password", *sorted(identity.provider.value for identity in identities))
        return cls(profile=OwnerProfile.of(owner, now, plan), sign_in_methods=methods)


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
    #: (code, name, accounts on record), in the plans' order.
    by_plan: tuple[tuple[str, str, int], ...]
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

    plan_id: str
    code: str
    name: str
    #: Free text; always empty for the trial.
    price: str
    #: Only for the trial: how long new accounts get it.
    trial_days: int | None
    typed_requests: bool
    #: Receipt scans and voice notes a month; None is no monthly limit.
    receipts: int | None
    voice: int | None
    voice_seconds: int
    visible: bool
    built_in: bool
    is_trial: bool
    #: Accounts on this plan on record.
    accounts: int

    @classmethod
    def of(cls, plan: PlanDefinition, *, accounts: int, trial_days: int) -> PlanCard:
        return cls(
            plan_id=str(plan.id),
            code=str(plan.code),
            name=plan.name,
            price=plan.price,
            trial_days=trial_days if plan.is_trial else None,
            typed_requests=plan.typed_requests,
            receipts=plan.allowance.receipts,
            voice=plan.allowance.voice,
            voice_seconds=plan.allowance.voice_seconds,
            visible=plan.visible,
            built_in=plan.built_in,
            is_trial=plan.is_trial,
            accounts=accounts,
        )
