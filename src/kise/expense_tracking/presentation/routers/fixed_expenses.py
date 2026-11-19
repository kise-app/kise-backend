"""The Fixed Monthly Expenses router.

``/api/fixed-expenses``                       list / schedule commitments
``/api/fixed-expenses/occurrences``           what is owed in one month, with totals
``/api/fixed-expenses/{id}``                  get / edit / delete one commitment
``/api/fixed-expenses/{id}/pause|resume``     stop or restart it being owed
``/api/fixed-expenses/{id}/settlements``      mark a month paid (POST) or undo it (DELETE)

``/occurrences`` is declared before ``/{id}`` so the literal path wins.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Response, status

from kise.expense_tracking.application.commands import (
    OccurrencesQuery,
    PeriodInput,
    UnsettleOccurrenceCommand,
)
from kise.expense_tracking.presentation.routers.fixed_expense_schemas import (
    FixedExpenseResponse,
    OccurrenceMonthResponse,
    OccurrenceResponse,
    ScheduleFixedExpenseRequest,
    SettleOccurrenceRequest,
    UpdateFixedExpenseRequest,
)
from kise.platform.api.dependencies import CurrentOwner, OwnerServicesDep
from kise.shared_kernel.domain.identifiers import FixedExpenseId

router = APIRouter(prefix="/api/fixed-expenses", tags=["fixed-expenses"])


@router.get("", response_model=list[FixedExpenseResponse])
def list_fixed_expenses(
    owner: CurrentOwner, services: OwnerServicesDep
) -> list[FixedExpenseResponse]:
    return [FixedExpenseResponse.of(v) for v in services.fixed_expenses.list(owner.owner_id)]


@router.post("", response_model=FixedExpenseResponse, status_code=status.HTTP_201_CREATED)
def schedule_fixed_expense(
    request: ScheduleFixedExpenseRequest, owner: CurrentOwner, services: OwnerServicesDep
) -> FixedExpenseResponse:
    return FixedExpenseResponse.of(
        services.fixed_expenses.schedule(request.to_command(owner.owner_id))
    )


@router.get("/occurrences", response_model=OccurrenceMonthResponse)
def occurrences_for_period(
    owner: CurrentOwner,
    services: OwnerServicesDep,
    period_year: int = Query(ge=1),
    period_month: int = Query(ge=1, le=13),
    period_calendar: str = Query(default="ethiopian"),
) -> OccurrenceMonthResponse:
    query = OccurrencesQuery(
        owner_id=owner.owner_id,
        period=PeriodInput(year=period_year, month=period_month, calendar=period_calendar),
    )
    return OccurrenceMonthResponse.of(services.fixed_expenses.occurrences_for(query))


@router.get("/{fixed_expense_id}", response_model=FixedExpenseResponse)
def get_fixed_expense(
    fixed_expense_id: str, owner: CurrentOwner, services: OwnerServicesDep
) -> FixedExpenseResponse:
    view = services.fixed_expenses.get(owner.owner_id, FixedExpenseId.parse(fixed_expense_id))
    return FixedExpenseResponse.of(view)


@router.patch("/{fixed_expense_id}", response_model=FixedExpenseResponse)
def update_fixed_expense(
    fixed_expense_id: str,
    request: UpdateFixedExpenseRequest,
    owner: CurrentOwner,
    services: OwnerServicesDep,
) -> FixedExpenseResponse:
    command = request.to_command(owner.owner_id, FixedExpenseId.parse(fixed_expense_id))
    return FixedExpenseResponse.of(services.fixed_expenses.update(command))


@router.delete("/{fixed_expense_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_fixed_expense(
    fixed_expense_id: str, owner: CurrentOwner, services: OwnerServicesDep
) -> Response:
    services.fixed_expenses.delete(owner.owner_id, FixedExpenseId.parse(fixed_expense_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{fixed_expense_id}/pause", response_model=FixedExpenseResponse)
def pause_fixed_expense(
    fixed_expense_id: str, owner: CurrentOwner, services: OwnerServicesDep
) -> FixedExpenseResponse:
    view = services.fixed_expenses.pause(owner.owner_id, FixedExpenseId.parse(fixed_expense_id))
    return FixedExpenseResponse.of(view)


@router.post("/{fixed_expense_id}/resume", response_model=FixedExpenseResponse)
def resume_fixed_expense(
    fixed_expense_id: str, owner: CurrentOwner, services: OwnerServicesDep
) -> FixedExpenseResponse:
    view = services.fixed_expenses.resume(owner.owner_id, FixedExpenseId.parse(fixed_expense_id))
    return FixedExpenseResponse.of(view)


@router.post(
    "/{fixed_expense_id}/settlements",
    response_model=OccurrenceResponse,
    status_code=status.HTTP_201_CREATED,
)
def settle_occurrence(
    fixed_expense_id: str,
    request: SettleOccurrenceRequest,
    owner: CurrentOwner,
    services: OwnerServicesDep,
) -> OccurrenceResponse:
    command = request.to_command(owner.owner_id, FixedExpenseId.parse(fixed_expense_id))
    return OccurrenceResponse.of(services.fixed_expenses.settle(command))


@router.delete("/{fixed_expense_id}/settlements", response_model=OccurrenceResponse)
def unsettle_occurrence(
    fixed_expense_id: str,
    owner: CurrentOwner,
    services: OwnerServicesDep,
    period_year: int = Query(ge=1),
    period_month: int = Query(ge=1, le=13),
    period_calendar: str = Query(default="ethiopian"),
) -> OccurrenceResponse:
    command = UnsettleOccurrenceCommand(
        owner_id=owner.owner_id,
        fixed_expense_id=FixedExpenseId.parse(fixed_expense_id),
        period=PeriodInput(year=period_year, month=period_month, calendar=period_calendar),
    )
    return OccurrenceResponse.of(services.fixed_expenses.unsettle(command))
