"""The Assistant's view of the Owner's spending, built over the Expense Tracking services.

The Assistant context declares ``SpendingContext`` as a port and never imports Expense Tracking.
This adapter lives in the composition root — the one place allowed to know both — and translates:
categories and units into the Assistant's ``Catalog``, and monthly summaries into plain facts the
language model can quote (amounts as major-unit strings, months named in both languages).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from kise.assistant.domain.drafts import Catalog, CategoryOption, UnitOption
from kise.expense_tracking.application.commands import MonthlySummaryQuery, PeriodInput
from kise.platform.container import RequestServices
from kise.shared_kernel.domain.calendar.calendar_kind import CalendarKind
from kise.shared_kernel.domain.calendar.period import Period
from kise.shared_kernel.domain.identifiers import OwnerId
from kise.shared_kernel.domain.money import Money


def _major(money: Money | None) -> str | None:
    if money is None:
        return None
    return f"{money.major_units:.2f}"


class ExpenseTrackingSpending:
    def __init__(self, services: RequestServices) -> None:
        self._services = services

    def catalog(self, owner_id: OwnerId) -> Catalog:
        categories = tuple(
            CategoryOption(id=str(view.category_id), name=view.name, name_am=view.name_am)
            for view in self._services.categories.list(owner_id)
        )
        units = tuple(
            UnitOption(id=str(view.unit_id), code=view.code, name=view.name)
            for view in self._services.units.list()
        )
        return Catalog(categories=categories, units=units)

    def month_facts(
        self, owner_id: OwnerId, calendar: CalendarKind, today: date, months: int
    ) -> list[dict[str, Any]]:
        current = Period.from_date(today, calendar)
        facts: list[dict[str, Any]] = []
        for back in range(max(1, months)):
            period = current.shift(-back)
            summary = self._services.summary.for_period(
                MonthlySummaryQuery(
                    owner_id=owner_id,
                    period=PeriodInput(
                        year=period.year, month=period.month, calendar=period.calendar
                    ),
                )
            )
            first, last = period.gregorian_span
            facts.append(
                {
                    "month": period.label("en"),
                    "month_am": period.label("am"),
                    "is_current_month": back == 0,
                    "days_in_month": period.length_in_days,
                    "days_so_far": ((today - first).days + 1) if back == 0 else None,
                    "gregorian_span": f"{first.isoformat()} to {last.isoformat()}",
                    "grand_total": _major(summary.grand_total),
                    "dynamic_total": _major(summary.dynamic_total),
                    "expense_count": summary.expense_count,
                    "committed": _major(summary.committed),
                    "settled": _major(summary.settled),
                    "outstanding": _major(summary.outstanding),
                    "budget": _major(summary.budget),
                    "categories": [
                        {
                            "name": c.name,
                            "name_am": c.name_am,
                            "total": _major(c.total),
                            "fixed_part": _major(c.committed),
                            "expenses": c.expense_count,
                            "limit": _major(c.budget),
                        }
                        for c in summary.categories
                    ],
                }
            )
        return facts
