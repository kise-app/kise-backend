"""The composition container: the one object that knows how every piece is wired together.

Nothing outside ``platform/`` constructs an adapter or a service. A router asks the container (via a
FastAPI dependency) for the service it needs, and the container hands back one bound to a fresh
Unit of Work for that request. This is the single place the three contexts are allowed to meet.

The container owns the *singletons* — the engine, the session factory, the event bus, the stateless
security adapters — and knows how to build the *per-request* things: a Unit of Work and the services
that share it, so everything one HTTP call does commits or rolls back together.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from kise.assistant.application.ports import AudioInspector, LanguageModel, RateLimiter
from kise.assistant.infrastructure.adapters import DisabledLanguageModel, InMemoryRateLimiter
from kise.assistant.infrastructure.audio import HeaderAudioInspector
from kise.assistant.infrastructure.gemini import GeminiLanguageModel
from kise.expense_tracking.application.services import (
    BudgetService,
    CategoryService,
    ExpenseService,
    FixedExpenseService,
    ItemService,
    ItemUsageService,
    MonthlySummaryService,
    UnitService,
)
from kise.expense_tracking.domain.models import default_units
from kise.identity.application.ports import SocialVerifier
from kise.identity.application.services import (
    AccountAdministrationService,
    AdministratorService,
    OwnerService,
    PlanService,
)
from kise.identity.application.services.plans import seed_default_plans
from kise.identity.domain.events import OwnerRegistered
from kise.identity.infrastructure.bcrypt_hasher import BcryptPasswordHasher
from kise.identity.infrastructure.jwt_token_service import JwtTokenService
from kise.identity.infrastructure.social import (
    AppleVerifier,
    FacebookVerifier,
    GoogleVerifier,
    ProviderSocialVerifier,
)
from kise.platform.admin.sessions import AdminSessions
from kise.platform.clock import SystemClock
from kise.platform.config import Settings
from kise.platform.database import build_engine, build_session_factory, create_all
from kise.platform.event_bus import InProcessEventBus
from kise.platform.subscribers import make_seed_default_categories
from kise.platform.unit_of_work import KiseUnitOfWork, build_unit_of_work_factory
from kise.shared_kernel.application.ports import Clock

logger = logging.getLogger("kise.platform")


@dataclass(slots=True)
class RequestServices:
    """Every application service a request might need, all sharing one Unit of Work.

    Handed to a route by the ``services`` dependency and thrown away when the request ends. Because
    the services share ``uow``, a route that touches two of them still commits atomically.
    """

    uow: KiseUnitOfWork
    admin: AccountAdministrationService
    administrators: AdministratorService
    owners: OwnerService
    plans: PlanService
    categories: CategoryService
    expenses: ExpenseService
    items: ItemService
    units: UnitService
    item_usage: ItemUsageService
    fixed_expenses: FixedExpenseService
    summary: MonthlySummaryService
    budgets: BudgetService


class Container:
    """Holds the singletons and builds per-request services."""

    def __init__(
        self,
        settings: Settings,
        *,
        engine: Engine | None = None,
        language_model: LanguageModel | None = None,
        clock: Clock | None = None,
        social_verifier: SocialVerifier | None = None,
    ) -> None:
        self._settings = settings
        self._engine = engine or build_engine(settings.database_url, echo=settings.debug)
        self._session_factory: sessionmaker[Session] = build_session_factory(self._engine)

        self._clock: Clock = clock or SystemClock()
        self._hasher = BcryptPasswordHasher()
        self._tokens = JwtTokenService(
            settings.secret_key, expire_minutes=settings.access_token_expire_minutes
        )

        self._language_model, self._assistant_enabled = self._build_language_model(
            settings, language_model
        )
        self._rate_limiter: RateLimiter = InMemoryRateLimiter(settings.assistant_hourly_limit)
        self._audio_inspector = HeaderAudioInspector()
        # Admin portal: its own session cookie, and a sign-in throttle of 10 tries per 15 minutes
        # per address.
        self._admin_sessions = AdminSessions(
            settings.secret_key, hours=settings.admin_session_hours
        )
        self._admin_login_limiter = InMemoryRateLimiter(10, window_seconds=15 * 60)
        self._social: SocialVerifier = social_verifier or ProviderSocialVerifier(
            google=GoogleVerifier(settings.google_client_id_list)
            if settings.google_client_id_list
            else None,
            apple=AppleVerifier(settings.apple_client_id_list)
            if settings.apple_client_id_list
            else None,
            facebook=FacebookVerifier(settings.facebook_app_id, settings.facebook_app_secret),
        )

        self._event_bus = InProcessEventBus()
        self._uow_factory = build_unit_of_work_factory(self._session_factory, self._event_bus)
        self._register_subscribers()

    @staticmethod
    def _build_language_model(
        settings: Settings, override: LanguageModel | None
    ) -> tuple[LanguageModel, bool]:
        if override is not None:
            return override, not isinstance(override, DisabledLanguageModel)
        if settings.assistant_enabled:
            return GeminiLanguageModel(settings.gemini_api_key, model=settings.gemini_model), True
        return DisabledLanguageModel(), False

    # -- singletons -----------------------------------------------------
    @property
    def language_model(self) -> LanguageModel:
        return self._language_model

    @property
    def rate_limiter(self) -> RateLimiter:
        return self._rate_limiter

    @property
    def audio_inspector(self) -> AudioInspector:
        return self._audio_inspector

    @property
    def assistant_enabled(self) -> bool:
        return self._assistant_enabled

    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def admin_sessions(self) -> AdminSessions:
        return self._admin_sessions

    @property
    def admin_login_limiter(self) -> RateLimiter:
        return self._admin_login_limiter

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def engine(self) -> Engine:
        return self._engine

    @property
    def tokens(self) -> JwtTokenService:
        return self._tokens

    def create_schema(self) -> None:
        """Create tables directly. Used for tests and first-run; Alembic owns real deployments."""
        create_all(self._engine)
        self.seed_plans()

    def seed_plans(self) -> int:
        """Create the default plans once, if there are none. Idempotent, like ``seed_units``."""
        uow = self.open_unit_of_work()
        try:
            return seed_default_plans(uow.plans, uow, self._clock.now())
        finally:
            uow.session.close()

    def seed_units(self) -> int:
        """Seed the default units once, if the table is empty. Idempotent.

        Units are global, so this runs once at application startup rather than per-owner. Safe to
        call on every boot — it does nothing when units already exist.
        """
        uow = self.open_unit_of_work()
        try:
            service = UnitService(units=uow.units, uow=uow)
            return service.seed_defaults(default_units())
        finally:
            uow.session.close()

    # -- wiring ---------------------------------------------------------
    def _register_subscribers(self) -> None:
        self._event_bus.subscribe(
            OwnerRegistered, make_seed_default_categories(self._uow_factory)
        )

    # -- per-request ----------------------------------------------------
    def open_unit_of_work(self) -> KiseUnitOfWork:
        return self._uow_factory()

    def build_services(
        self, uow: KiseUnitOfWork, *, currency: str | None = None
    ) -> RequestServices:
        """Assemble the services for one request, all sharing ``uow``.

        ``currency`` is the signed-in Owner's currency, so money they record is written in the
        currency they registered with. Unauthenticated routes (register, login) fall back to the
        configured default.
        """
        currency = currency or self._settings.default_currency
        owners = OwnerService(
            owners=uow.owners,
            hasher=self._hasher,
            tokens=self._tokens,
            clock=self._clock,
            uow=uow,
            linked_identities=uow.linked_identities,
            social=self._social,
            plan_settings=uow.plan_settings,
            plans=uow.plans,
        )
        categories = CategoryService(
            categories=uow.categories,
            expenses=uow.expenses,
            fixed_expenses=uow.fixed_expenses,
            uow=uow,
        )
        expenses = ExpenseService(
            expenses=uow.expenses,
            categories=uow.categories,
            uow=uow,
            items=uow.items,
            units=uow.units,
            currency=currency,
        )
        items = ItemService(
            items=uow.items,
            units=uow.units,
            expenses=uow.expenses,
            uow=uow,
        )
        units = UnitService(units=uow.units, uow=uow)
        item_usage = ItemUsageService(
            expenses=uow.expenses,
            items=uow.items,
            units=uow.units,
            currency=currency,
        )
        fixed_expenses = FixedExpenseService(
            fixed_expenses=uow.fixed_expenses,
            categories=uow.categories,
            uow=uow,
            clock=self._clock,
            currency=currency,
        )
        summary = MonthlySummaryService(
            expenses=uow.expenses,
            fixed_expenses=uow.fixed_expenses,
            categories=uow.categories,
            budgets=uow.budgets,
            currency=currency,
        )
        budgets = BudgetService(
            budgets=uow.budgets,
            categories=uow.categories,
            uow=uow,
            currency=currency,
        )
        admin = AccountAdministrationService(
            owners=uow.owners,
            linked_identities=uow.linked_identities,
            plan_settings=uow.plan_settings,
            payment_settings=uow.payment_settings,
            plans=uow.plans,
            clock=self._clock,
            uow=uow,
        )
        administrators = AdministratorService(
            administrators=uow.administrators,
            hasher=self._hasher,
            clock=self._clock,
            uow=uow,
        )
        return RequestServices(
            uow=uow,
            admin=admin,
            administrators=administrators,
            plans=PlanService(payment_settings=uow.payment_settings, plans=uow.plans),
            owners=owners,
            categories=categories,
            expenses=expenses,
            items=items,
            units=units,
            item_usage=item_usage,
            fixed_expenses=fixed_expenses,
            summary=summary,
            budgets=budgets,
        )

    def owner_service_for_token(self) -> tuple[KiseUnitOfWork, OwnerService]:
        """A standalone OwnerService for resolving a bearer token in the auth dependency.

        Kept separate so token resolution does not need to build the whole service set.
        """
        uow = self.open_unit_of_work()
        service = OwnerService(
            owners=uow.owners,
            hasher=self._hasher,
            tokens=self._tokens,
            clock=self._clock,
            uow=uow,
            linked_identities=uow.linked_identities,
            social=self._social,
            plan_settings=uow.plan_settings,
            plans=uow.plans,
        )
        return uow, service


def build_container(settings: Settings) -> Container:
    container = Container(settings)
    if settings.is_secret_key_insecure:
        logger.warning(
            "SECRET_KEY is the insecure default or too short. Set a long random value "
            "before deploying; tokens signed with it are trivially forgeable."
        )
    return container
