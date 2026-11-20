"""Plans as the rest of Identity needs them: which definition applies to an account, and which
plans the app offers.

``PlanCatalogue`` resolves the plan in force (a plan past its end is Basic) to its definition.
``PlanService`` is what anyone signed in may ask: how to pay, and for which plans.
"""

from __future__ import annotations

from datetime import datetime

from kise.identity.domain.models import (
    BASIC,
    PaymentInstructions,
    PlanDefinition,
    Subscription,
    default_plans,
)
from kise.identity.infrastructure.persistence.repository import (
    SqlAlchemyPaymentSettingsRepository,
    SqlAlchemyPlanRepository,
)
from kise.shared_kernel.application.ports import UnitOfWork


class PlanCatalogue:
    def __init__(self, plans: SqlAlchemyPlanRepository) -> None:
        self._plans = plans

    def in_force(self, subscription: Subscription, now: datetime) -> PlanDefinition | None:
        """The definition of the plan an account is on at ``now``. A plan that is gone (the rules
        prevent it, but data outlives rules) counts as Basic."""
        code = subscription.effective(now)
        found = self._plans.find_by_code(code)
        if found is None and code != BASIC:
            found = self._plans.find_by_code(BASIC)
        return found


class PlanService:
    def __init__(
        self,
        payment_settings: SqlAlchemyPaymentSettingsRepository,
        plans: SqlAlchemyPlanRepository,
    ) -> None:
        self._payment_settings = payment_settings
        self._plans = plans

    def payment_instructions(self) -> PaymentInstructions:
        return self._payment_settings.get()

    def offered(self) -> list[PlanDefinition]:
        """The plans the app shows in its upgrade sheet, in the admin's order."""
        return [plan for plan in self._plans.list() if plan.visible]


def seed_default_plans(plans: SqlAlchemyPlanRepository, uow: UnitOfWork, now: datetime) -> int:
    """Create Trial, Basic and Pro if there are no plans at all. Idempotent: run on every boot."""
    if plans.list():
        return 0
    created = default_plans(now)
    for plan in created:
        plans.add(plan)
    uow.commit()
    return len(created)
