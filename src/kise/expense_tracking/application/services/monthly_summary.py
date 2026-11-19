"""The monthly summary — the numbers behind the dashboard.

"How much did I spend in ሐምሌ?" has three parts, and this read model answers all of them at once so
the app does one request per month rather than paging through every expense:

* **Dynamic spending** — the month's expenses, summed in SQL, per day and per category.
* **Fixed commitments** — every active Fixed Monthly Expense projected into the Period, split into
  committed / settled / outstanding. Occurrences are computed, not stored, so this goes through the
  aggregates (a few dozen rows at most) rather than SQL.
* **Last month's grand total**, for the "x% more than last month" line.
* **Budgets** — the overall monthly limit and each category's, so the app can warn when a month or
  a category runs hot. A category with a budget but no spending yet still appears, at zero.

The Period resolves to its own Gregorian span, so an Ethiopian month works without the client doing
calendar arithmetic, and ``daily`` is indexed by the day of *that* calendar's month.
"""

from __future__ import annotations

from kise.expense_tracking.application.commands import MonthlySummaryQuery
from kise.expense_tracking.application.views import CategorySpend, MonthlySummary
from kise.expense_tracking.domain.models import Category, Occurrence
from kise.expense_tracking.infrastructure.persistence.repositories import (
    SqlAlchemyBudgetRepository,
    SqlAlchemyCategoryRepository,
    SqlAlchemyExpenseRepository,
    SqlAlchemyFixedExpenseRepository,
)
from kise.shared_kernel.domain.calendar.period import Period
from kise.shared_kernel.domain.identifiers import CategoryId, OwnerId
from kise.shared_kernel.domain.money import Money


class MonthlySummaryService:
    def __init__(
        self,
        expenses: SqlAlchemyExpenseRepository,
        fixed_expenses: SqlAlchemyFixedExpenseRepository,
        categories: SqlAlchemyCategoryRepository,
        budgets: SqlAlchemyBudgetRepository,
        *,
        currency: str = "ETB",
    ) -> None:
        self._expenses = expenses
        self._fixed = fixed_expenses
        self._categories = categories
        self._budgets = budgets
        self._currency = currency

    def _money(self, minor: int) -> Money:
        return Money(minor, self._currency)

    def _occurrences(self, owner_id: OwnerId, period: Period) -> list[Occurrence]:
        found: list[Occurrence] = []
        for commitment in self._fixed.list_for_owner(owner_id, include_paused=False):
            occurrence = commitment.occurrence_for(period)
            if occurrence is not None:
                found.append(occurrence)
        return found

    def _grand_total_minor(self, owner_id: OwnerId, period: Period) -> int:
        since, until = period.gregorian_span
        dynamic_minor, _ = self._expenses.totals(owner_id, since, until)
        committed_minor = sum(
            occurrence.amount.minor_units for occurrence in self._occurrences(owner_id, period)
        )
        return dynamic_minor + committed_minor

    def for_period(self, query: MonthlySummaryQuery) -> MonthlySummary:
        owner_id = query.owner_id
        period = query.period.resolve()
        since, until = period.gregorian_span

        dynamic_minor, expense_count = self._expenses.totals(owner_id, since, until)

        daily = [0] * period.length_in_days
        for day, amount_minor in self._expenses.daily_totals(owner_id, since, until):
            index = (day - since).days
            if 0 <= index < len(daily):
                daily[index] += amount_minor

        occurrences = self._occurrences(owner_id, period)
        committed_minor = sum(o.amount.minor_units for o in occurrences)
        settled_minor = sum(o.amount.minor_units for o in occurrences if o.is_settled)

        # Per category: dynamic spending and committed fixed amounts, merged.
        dynamic_by_category: dict[CategoryId, tuple[int, int]] = {
            CategoryId(category_uuid): (amount_minor, count)
            for category_uuid, amount_minor, count in self._expenses.category_totals(
                owner_id, since, until
            )
        }
        committed_by_category: dict[CategoryId, int] = {}
        for occurrence in occurrences:
            committed_by_category[occurrence.category_id] = (
                committed_by_category.get(occurrence.category_id, 0)
                + occurrence.amount.minor_units
            )

        overall_budget: Money | None = None
        budget_by_category: dict[CategoryId, Money] = {}
        for budget in self._budgets.list_for_owner(owner_id):
            if budget.category_id is None:
                overall_budget = budget.amount
            else:
                budget_by_category[budget.category_id] = budget.amount

        categories: dict[CategoryId, Category] = {
            category.id: category
            for category in self._categories.list_for_owner(owner_id, include_archived=True)
        }
        spends: list[CategorySpend] = []
        for category_id in (
            dynamic_by_category.keys() | committed_by_category.keys() | budget_by_category.keys()
        ):
            category = categories.get(category_id)
            if category is None:  # pragma: no cover - referential integrity holds
                continue
            amount_minor, count = dynamic_by_category.get(category_id, (0, 0))
            committed_here = committed_by_category.get(category_id, 0)
            if category.is_archived and amount_minor == 0 and committed_here == 0:
                continue  # a leftover budget on an archived category is not worth a row
            spends.append(
                CategorySpend(
                    category_id=category.id,
                    name=category.name,
                    name_am=category.name_am,
                    color=category.color.value,
                    icon=category.icon,
                    dynamic=self._money(amount_minor),
                    committed=self._money(committed_by_category.get(category_id, 0)),
                    expense_count=count,
                    budget=budget_by_category.get(category_id),
                )
            )
        # Largest first, name as a tiebreaker so the order is stable.
        spends.sort(key=lambda spend: (-spend.total.minor_units, spend.name.casefold()))

        return MonthlySummary(
            period=period,
            dynamic_total=self._money(dynamic_minor),
            expense_count=expense_count,
            committed=self._money(committed_minor),
            settled=self._money(settled_minor),
            outstanding=self._money(committed_minor - settled_minor),
            daily=tuple(self._money(amount) for amount in daily),
            categories=tuple(spends),
            previous_total=self._money(self._grand_total_minor(owner_id, period.previous)),
            budget=overall_budget,
        )
