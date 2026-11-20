"""What anyone signed in may ask about plans: how to pay for Pro, and what their plan includes.

Read-only, and apart from ``AccountAdministrationService`` (which writes these details) because the
app calls it for every account, while only the admin portal may reach the other.
"""

from __future__ import annotations

from kise.identity.domain.models import PaymentInstructions, Plan, PlanAllowance
from kise.identity.infrastructure.persistence.repository import (
    SqlAlchemyPaymentSettingsRepository,
    SqlAlchemyPlanSettingsRepository,
)


class PlanService:
    def __init__(
        self,
        payment_settings: SqlAlchemyPaymentSettingsRepository,
        plan_settings: SqlAlchemyPlanSettingsRepository,
    ) -> None:
        self._payment_settings = payment_settings
        self._plan_settings = plan_settings

    def allowance_for(self, plan: Plan | str) -> PlanAllowance:
        """Receipt scans and voice notes ``plan`` includes a month (pass the plan in force)."""
        return self._plan_settings.get().allowance_for(plan)

    def payment_instructions(self) -> PaymentInstructions:
        return self._payment_settings.get()
