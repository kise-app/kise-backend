"""What anyone signed in may ask about plans: how to pay for Pro.

Read-only, and apart from ``AccountAdministrationService`` (which writes these details) because the
app calls it for every account, while only the admin portal may reach the other.
"""

from __future__ import annotations

from kise.identity.domain.models import PaymentInstructions
from kise.identity.infrastructure.persistence.repository import (
    SqlAlchemyPaymentSettingsRepository,
)


class PlanService:
    def __init__(self, payment_settings: SqlAlchemyPaymentSettingsRepository) -> None:
        self._payment_settings = payment_settings

    def payment_instructions(self) -> PaymentInstructions:
        return self._payment_settings.get()
