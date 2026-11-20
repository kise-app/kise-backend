"""Plans, for the signed-in person: how to pay for Pro.

``GET /api/plans/payment``   where to pay and how to send the screenshot (set in the admin portal)

The plan itself comes with the profile (``GET /api/auth/me``).
"""

from __future__ import annotations

from fastapi import APIRouter

from kise.identity.presentation.schemas import PaymentInstructionsResponse
from kise.platform.api.dependencies import CurrentOwner, ServicesDep

router = APIRouter(prefix="/api/plans", tags=["plans"])


@router.get("/payment", response_model=PaymentInstructionsResponse)
def payment(current_owner: CurrentOwner, services: ServicesDep) -> PaymentInstructionsResponse:
    return PaymentInstructionsResponse.of(
        services.plans.payment_instructions(), services.plans.offered(), current_owner
    )
