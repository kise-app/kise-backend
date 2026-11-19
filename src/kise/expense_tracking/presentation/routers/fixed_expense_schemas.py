"""Wire schemas for Fixed Monthly Expenses, their Occurrences, and the monthly summary.

Same contract as everywhere else: money is ``{amount_minor, currency}``, dates go out in both
calendars (``SpendDate.describe()``), and periods go out with every label the client shows
(``Period.labels()``) plus ``year``/``month`` so the client can send the same Period back — which is
how "mark ሐምሌ as paid" round-trips without the app doing calendar arithmetic.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from kise.expense_tracking.application.commands import (
    ScheduleFixedExpenseCommand,
    SettleOccurrenceCommand,
    UpdateFixedExpenseCommand,
)
from kise.expense_tracking.application.views import (
    CategorySpend,
    FixedExpenseView,
    MonthlySummary,
    OccurrenceMonth,
    OccurrenceView,
)
from kise.expense_tracking.domain.models import MAX_DUE_DAY
from kise.expense_tracking.presentation.schemas import MoneyView
from kise.platform.api.schemas import DateInputSchema, PeriodInputSchema
from kise.shared_kernel.domain.calendar.period import Period
from kise.shared_kernel.domain.calendar.spend_date import SpendDate
from kise.shared_kernel.domain.identifiers import CategoryId, FixedExpenseId, OwnerId


def period_payload(period: Period) -> dict[str, Any]:
    """``Period.labels()`` plus the raw year and month, so the client can echo the Period back."""
    return {**period.labels(), "year": period.year, "month": period.month}


# -- requests -----------------------------------------------------------


class ScheduleFixedExpenseRequest(BaseModel):
    category_id: str
    title: str = Field(min_length=1, max_length=80, examples=["Rent"])
    amount_minor: int = Field(gt=0, description="Integer minor units, e.g. santim for ETB")
    start_period: PeriodInputSchema
    end_period: PeriodInputSchema | None = None
    due_day: int = Field(default=1, ge=1, le=MAX_DUE_DAY)
    note: str | None = Field(default=None, max_length=280)

    def to_command(self, owner_id: OwnerId) -> ScheduleFixedExpenseCommand:
        return ScheduleFixedExpenseCommand(
            owner_id=owner_id,
            category_id=CategoryId.parse(self.category_id),
            title=self.title,
            amount_minor=self.amount_minor,
            start_period=self.start_period.to_input(),
            end_period=self.end_period.to_input() if self.end_period else None,
            due_day=self.due_day,
            note=self.note,
        )


class UpdateFixedExpenseRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=80)
    amount_minor: int | None = Field(default=None, gt=0)
    category_id: str | None = None
    due_day: int | None = Field(default=None, ge=1, le=MAX_DUE_DAY)
    note: str | None = Field(default=None, max_length=280)
    end_period: PeriodInputSchema | None = None
    clear_end_period: bool = False

    def to_command(
        self, owner_id: OwnerId, fixed_expense_id: FixedExpenseId
    ) -> UpdateFixedExpenseCommand:
        return UpdateFixedExpenseCommand(
            owner_id=owner_id,
            fixed_expense_id=fixed_expense_id,
            title=self.title,
            amount_minor=self.amount_minor,
            category_id=CategoryId.parse(self.category_id) if self.category_id else None,
            due_day=self.due_day,
            note=self.note,
            end_period=self.end_period.to_input() if self.end_period else None,
            clear_end_period=self.clear_end_period,
        )


class SettleOccurrenceRequest(BaseModel):
    period: PeriodInputSchema
    settled_on: DateInputSchema | None = Field(
        default=None, description="When it was paid; defaults to today"
    )
    amount_minor: int | None = Field(
        default=None, gt=0, description="What was actually paid; defaults to the committed amount"
    )

    def to_command(
        self, owner_id: OwnerId, fixed_expense_id: FixedExpenseId
    ) -> SettleOccurrenceCommand:
        return SettleOccurrenceCommand(
            owner_id=owner_id,
            fixed_expense_id=fixed_expense_id,
            period=self.period.to_input(),
            settled_on=self.settled_on.to_input() if self.settled_on else None,
            amount_minor=self.amount_minor,
        )


# -- responses ----------------------------------------------------------


class FixedExpenseResponse(BaseModel):
    id: str
    category_id: str
    category_name: str | None
    category_name_am: str | None
    title: str
    amount: MoneyView
    anchor_calendar: str
    start_period: dict[str, Any]
    end_period: dict[str, Any] | None
    due_day: int
    note: str | None
    is_paused: bool
    settled_periods: list[dict[str, Any]]

    @classmethod
    def of(cls, view: FixedExpenseView) -> FixedExpenseResponse:
        return cls(
            id=str(view.fixed_expense_id),
            category_id=str(view.category_id),
            category_name=view.category_name,
            category_name_am=view.category_name_am,
            title=view.title,
            amount=MoneyView.of(view.amount),
            anchor_calendar=view.anchor_calendar.value,
            start_period=period_payload(view.start_period),
            end_period=period_payload(view.end_period) if view.end_period else None,
            due_day=view.due_day,
            note=view.note,
            is_paused=view.is_paused,
            settled_periods=[period_payload(p) for p in view.settled_periods],
        )


class OccurrenceResponse(BaseModel):
    fixed_expense_id: str
    category_id: str
    category_name: str | None
    category_name_am: str | None
    title: str
    period: dict[str, Any]
    due_date: dict[str, Any]
    amount: MoneyView
    is_settled: bool
    settled_on: dict[str, Any] | None

    @classmethod
    def of(cls, view: OccurrenceView) -> OccurrenceResponse:
        return cls(
            fixed_expense_id=str(view.fixed_expense_id),
            category_id=str(view.category_id),
            category_name=view.category_name,
            category_name_am=view.category_name_am,
            title=view.title,
            period=period_payload(view.period),
            due_date=SpendDate.from_gregorian(view.due_date).describe(),
            amount=MoneyView.of(view.amount),
            is_settled=view.is_settled,
            settled_on=(
                SpendDate.from_gregorian(view.settled_on).describe() if view.settled_on else None
            ),
        )


class OccurrenceMonthResponse(BaseModel):
    period: dict[str, Any]
    occurrences: list[OccurrenceResponse]
    committed: MoneyView
    settled: MoneyView
    outstanding: MoneyView

    @classmethod
    def of(cls, month: OccurrenceMonth) -> OccurrenceMonthResponse:
        return cls(
            period=period_payload(month.period),
            occurrences=[OccurrenceResponse.of(o) for o in month.occurrences],
            committed=MoneyView.of(month.committed),
            settled=MoneyView.of(month.settled),
            outstanding=MoneyView.of(month.outstanding),
        )


# -- monthly summary ----------------------------------------------------


class CategorySpendResponse(BaseModel):
    category_id: str
    name: str
    name_am: str | None
    color: str
    icon: str | None
    total: MoneyView
    dynamic: MoneyView
    committed: MoneyView
    expense_count: int
    budget: MoneyView | None = None

    @classmethod
    def of(cls, spend: CategorySpend) -> CategorySpendResponse:
        return cls(
            category_id=str(spend.category_id),
            name=spend.name,
            name_am=spend.name_am,
            color=spend.color,
            icon=spend.icon,
            total=MoneyView.of(spend.total),
            dynamic=MoneyView.of(spend.dynamic),
            committed=MoneyView.of(spend.committed),
            expense_count=spend.expense_count,
            budget=MoneyView.of(spend.budget) if spend.budget else None,
        )


class MonthlySummaryResponse(BaseModel):
    period: dict[str, Any]
    currency: str
    grand_total: MoneyView
    dynamic_total: MoneyView
    expense_count: int
    committed: MoneyView
    settled: MoneyView
    outstanding: MoneyView
    previous_total: MoneyView
    budget: MoneyView | None = Field(default=None, description="The overall monthly budget")
    daily_minor: list[int] = Field(
        description="One entry per day of the Period, dynamic spending only, in minor units"
    )
    categories: list[CategorySpendResponse]

    @classmethod
    def of(cls, summary: MonthlySummary) -> MonthlySummaryResponse:
        return cls(
            period=period_payload(summary.period),
            currency=summary.dynamic_total.currency,
            grand_total=MoneyView.of(summary.grand_total),
            dynamic_total=MoneyView.of(summary.dynamic_total),
            expense_count=summary.expense_count,
            committed=MoneyView.of(summary.committed),
            settled=MoneyView.of(summary.settled),
            outstanding=MoneyView.of(summary.outstanding),
            previous_total=MoneyView.of(summary.previous_total),
            budget=MoneyView.of(summary.budget) if summary.budget else None,
            daily_minor=[day.minor_units for day in summary.daily],
            categories=[CategorySpendResponse.of(c) for c in summary.categories],
        )
