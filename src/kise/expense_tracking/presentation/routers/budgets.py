"""The budgets router: monthly spending limits.

``GET    /api/budgets``                              every budget the owner has set
``PUT    /api/budgets/overall``                      set the overall monthly budget
``DELETE /api/budgets/overall``                      remove it
``PUT    /api/budgets/categories/{category_id}``     set one category's monthly limit
``DELETE /api/budgets/categories/{category_id}``     remove it

``PUT`` because setting is idempotent: there is at most one of each, and setting again changes it.
How a month is doing against them comes back in ``GET /api/reports/summary``.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field

from kise.expense_tracking.application.commands import SetBudgetCommand
from kise.expense_tracking.application.views import BudgetView
from kise.expense_tracking.presentation.schemas import MoneyView
from kise.platform.api.dependencies import CurrentOwner, OwnerServicesDep
from kise.shared_kernel.domain.identifiers import CategoryId

router = APIRouter(prefix="/api/budgets", tags=["budgets"])


class SetBudgetRequest(BaseModel):
    amount_minor: int = Field(gt=0, description="Integer minor units per month")


class BudgetResponse(BaseModel):
    id: str
    category_id: str | None
    amount: MoneyView

    @classmethod
    def of(cls, view: BudgetView) -> BudgetResponse:
        return cls(
            id=str(view.budget_id),
            category_id=str(view.category_id) if view.category_id else None,
            amount=MoneyView.of(view.amount),
        )


@router.get("", response_model=list[BudgetResponse])
def list_budgets(owner: CurrentOwner, services: OwnerServicesDep) -> list[BudgetResponse]:
    return [BudgetResponse.of(v) for v in services.budgets.list(owner.owner_id)]


@router.put("/overall", response_model=BudgetResponse)
def set_overall_budget(
    request: SetBudgetRequest, owner: CurrentOwner, services: OwnerServicesDep
) -> BudgetResponse:
    command = SetBudgetCommand(owner_id=owner.owner_id, amount_minor=request.amount_minor)
    return BudgetResponse.of(services.budgets.set(command))


@router.delete("/overall", status_code=status.HTTP_204_NO_CONTENT)
def clear_overall_budget(owner: CurrentOwner, services: OwnerServicesDep) -> Response:
    services.budgets.clear(owner.owner_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/categories/{category_id}", response_model=BudgetResponse)
def set_category_budget(
    category_id: str,
    request: SetBudgetRequest,
    owner: CurrentOwner,
    services: OwnerServicesDep,
) -> BudgetResponse:
    command = SetBudgetCommand(
        owner_id=owner.owner_id,
        amount_minor=request.amount_minor,
        category_id=CategoryId.parse(category_id),
    )
    return BudgetResponse.of(services.budgets.set(command))


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def clear_category_budget(
    category_id: str, owner: CurrentOwner, services: OwnerServicesDep
) -> Response:
    services.budgets.clear(owner.owner_id, CategoryId.parse(category_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
