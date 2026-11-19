"""The Budget mapping contract."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from kise.expense_tracking.domain.models import Budget

__all__ = ["BudgetMapping"]


@runtime_checkable
class BudgetMapping(Protocol):
    """Translates the Budget aggregate between its domain form and its stored form."""

    @staticmethod
    def to_model(aggregate: Budget) -> Any: ...

    @staticmethod
    def to_domain(model: Any) -> Budget: ...

    @staticmethod
    def update_model(model: Any, aggregate: Budget) -> None: ...
