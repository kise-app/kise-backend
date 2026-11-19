"""The Fixed Monthly Expense application service.

Schedules commitments (rent, internet, school fees), edits them, pauses and resumes them, and
records which months were actually paid. The rules all live in the ``FixedExpense`` aggregate; this
service resolves inputs into value objects, checks the category belongs to the caller, and commits.

The read side worth naming is ``occurrences_for``: a commitment stores no rows for future months, so
"what do I owe in ሐምሌ?" is answered by projecting every commitment into that Period on demand.
"""

from __future__ import annotations

from kise.expense_tracking.application.commands import (
    OccurrencesQuery,
    ScheduleFixedExpenseCommand,
    SettleOccurrenceCommand,
    UnsettleOccurrenceCommand,
    UpdateFixedExpenseCommand,
)
from kise.expense_tracking.application.views import (
    FixedExpenseView,
    OccurrenceMonth,
    OccurrenceView,
)
from kise.expense_tracking.domain.models import Category, FixedExpense
from kise.expense_tracking.domain.policies import ensure_category_usable, ensure_owned_by
from kise.expense_tracking.infrastructure.persistence.repositories import (
    SqlAlchemyCategoryRepository,
    SqlAlchemyFixedExpenseRepository,
)
from kise.shared_kernel.application.ports import Clock, UnitOfWork
from kise.shared_kernel.domain.calendar.period import Period
from kise.shared_kernel.domain.identifiers import CategoryId, FixedExpenseId, OwnerId
from kise.shared_kernel.domain.money import Money, sum_money


class FixedExpenseService:
    def __init__(
        self,
        fixed_expenses: SqlAlchemyFixedExpenseRepository,
        categories: SqlAlchemyCategoryRepository,
        uow: UnitOfWork,
        clock: Clock,
        *,
        currency: str = "ETB",
    ) -> None:
        self._fixed = fixed_expenses
        self._categories = categories
        self._uow = uow
        self._clock = clock
        self._currency = currency

    # -- helpers --------------------------------------------------------
    def _usable_category(self, owner_id: OwnerId, category_id: CategoryId) -> Category:
        category = self._categories.get(owner_id, category_id)
        ensure_owned_by(owner_id, category)
        ensure_category_usable(category)
        return category

    def _categories_by_id(self, owner_id: OwnerId) -> dict[CategoryId, Category]:
        return {
            category.id: category
            for category in self._categories.list_for_owner(owner_id, include_archived=True)
        }

    def _load(self, owner_id: OwnerId, fixed_expense_id: FixedExpenseId) -> FixedExpense:
        commitment = self._fixed.get(owner_id, fixed_expense_id)
        ensure_owned_by(owner_id, commitment)
        return commitment

    def _view(self, owner_id: OwnerId, commitment: FixedExpense) -> FixedExpenseView:
        category = self._categories.get(owner_id, commitment.category_id)
        return FixedExpenseView.of(commitment, category)

    # -- commands -------------------------------------------------------
    def schedule(self, command: ScheduleFixedExpenseCommand) -> FixedExpenseView:
        category = self._usable_category(command.owner_id, command.category_id)
        commitment = FixedExpense.schedule(
            owner_id=command.owner_id,
            category_id=category.id,
            title=command.title,
            amount=Money(command.amount_minor, self._currency),
            start_period=command.start_period.resolve(),
            end_period=command.end_period.resolve() if command.end_period else None,
            due_day=command.due_day,
            note=command.note,
        )
        self._fixed.add(commitment)
        self._uow.commit()
        return FixedExpenseView.of(commitment, category)

    def update(self, command: UpdateFixedExpenseCommand) -> FixedExpenseView:
        commitment = self._load(command.owner_id, command.fixed_expense_id)
        if command.title is not None:
            commitment.retitle(command.title)
        if command.amount_minor is not None:
            commitment.change_amount(Money(command.amount_minor, commitment.amount.currency))
        if command.category_id is not None:
            category = self._usable_category(command.owner_id, command.category_id)
            commitment.recategorise(category.id)
        if command.due_day is not None:
            commitment.change_due_day(command.due_day)
        if command.note is not None:
            commitment.amend_note(command.note)
        if command.clear_end_period:
            commitment.end_after(None)
        elif command.end_period is not None:
            # An end given in the other calendar is mapped onto the anchor calendar first.
            commitment.end_after(
                command.end_period.resolve().as_calendar(commitment.anchor_calendar)
            )
        self._uow.commit()
        return self._view(command.owner_id, commitment)

    def pause(self, owner_id: OwnerId, fixed_expense_id: FixedExpenseId) -> FixedExpenseView:
        commitment = self._load(owner_id, fixed_expense_id)
        commitment.pause()
        self._uow.commit()
        return self._view(owner_id, commitment)

    def resume(self, owner_id: OwnerId, fixed_expense_id: FixedExpenseId) -> FixedExpenseView:
        commitment = self._load(owner_id, fixed_expense_id)
        commitment.resume()
        self._uow.commit()
        return self._view(owner_id, commitment)

    def delete(self, owner_id: OwnerId, fixed_expense_id: FixedExpenseId) -> None:
        commitment = self._load(owner_id, fixed_expense_id)
        self._fixed.remove(commitment)
        self._uow.commit()

    def settle(self, command: SettleOccurrenceCommand) -> OccurrenceView:
        commitment = self._load(command.owner_id, command.fixed_expense_id)
        period = command.period.resolve()
        settled_on = (
            command.settled_on.resolve().gregorian
            if command.settled_on is not None
            else self._clock.today()
        )
        amount = (
            Money(command.amount_minor, commitment.amount.currency)
            if command.amount_minor is not None
            else None
        )
        commitment.settle(period, settled_on=settled_on, amount=amount)
        self._uow.commit()
        return self._occurrence_view(command.owner_id, commitment, period)

    def unsettle(self, command: UnsettleOccurrenceCommand) -> OccurrenceView:
        commitment = self._load(command.owner_id, command.fixed_expense_id)
        period = command.period.resolve()
        commitment.unsettle(period)
        self._uow.commit()
        return self._occurrence_view(command.owner_id, commitment, period)

    def _occurrence_view(
        self, owner_id: OwnerId, commitment: FixedExpense, period: Period
    ) -> OccurrenceView:
        occurrence = commitment.occurrence_for(period)
        if occurrence is None:  # pragma: no cover - settle/unsettle already checked activity
            raise RuntimeError("settled a period the commitment is not active in")
        category = self._categories.get(owner_id, commitment.category_id)
        return OccurrenceView.of(occurrence, category)

    # -- queries --------------------------------------------------------
    def get(self, owner_id: OwnerId, fixed_expense_id: FixedExpenseId) -> FixedExpenseView:
        return self._view(owner_id, self._load(owner_id, fixed_expense_id))

    def list(self, owner_id: OwnerId) -> list[FixedExpenseView]:
        by_id = self._categories_by_id(owner_id)
        return [
            FixedExpenseView.of(commitment, by_id.get(commitment.category_id))
            for commitment in self._fixed.list_for_owner(owner_id)
        ]

    def occurrences_for(self, query: OccurrencesQuery) -> OccurrenceMonth:
        """Everything owed in a Period, soonest due first, with the month's totals."""
        period = query.period.resolve()
        by_id = self._categories_by_id(query.owner_id)
        views: list[OccurrenceView] = []
        for commitment in self._fixed.list_for_owner(query.owner_id, include_paused=False):
            occurrence = commitment.occurrence_for(period)
            if occurrence is not None:
                views.append(OccurrenceView.of(occurrence, by_id.get(occurrence.category_id)))
        views.sort(key=lambda view: (view.due_date, view.title.casefold()))

        committed = sum_money([view.amount for view in views], self._currency)
        settled = sum_money([view.amount for view in views if view.is_settled], self._currency)
        return OccurrenceMonth(
            period=period,
            occurrences=tuple(views),
            committed=committed,
            settled=settled,
            outstanding=committed - settled,
        )
