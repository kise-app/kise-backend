"""The Budget application service — monthly limits, overall and per category.

Setting is idempotent: there is at most one overall budget and one per category, so ``set`` changes
the existing one when it exists and creates it otherwise. That "at most one" spans aggregates, which
is why it lives here rather than in ``Budget``.
"""

from __future__ import annotations

from kise.expense_tracking.application.commands import SetBudgetCommand
from kise.expense_tracking.application.views import BudgetView
from kise.expense_tracking.domain.models import Budget
from kise.expense_tracking.domain.policies import ensure_owned_by
from kise.expense_tracking.infrastructure.persistence.repositories import (
    SqlAlchemyBudgetRepository,
    SqlAlchemyCategoryRepository,
)
from kise.shared_kernel.application.ports import UnitOfWork
from kise.shared_kernel.domain.identifiers import CategoryId, OwnerId
from kise.shared_kernel.domain.money import Money


class BudgetService:
    def __init__(
        self,
        budgets: SqlAlchemyBudgetRepository,
        categories: SqlAlchemyCategoryRepository,
        uow: UnitOfWork,
        *,
        currency: str = "ETB",
    ) -> None:
        self._budgets = budgets
        self._categories = categories
        self._uow = uow
        self._currency = currency

    def list(self, owner_id: OwnerId) -> list[BudgetView]:
        return [BudgetView.of(budget) for budget in self._budgets.list_for_owner(owner_id)]

    def set(self, command: SetBudgetCommand) -> BudgetView:
        if command.category_id is not None:
            # Raises CategoryNotFound for someone else's category: the repository is owner-scoped.
            category = self._categories.get(command.owner_id, command.category_id)
            ensure_owned_by(command.owner_id, category)
        amount = Money(command.amount_minor, self._currency)
        budget = self._budgets.find(command.owner_id, command.category_id)
        if budget is None:
            budget = Budget.set(
                owner_id=command.owner_id, amount=amount, category_id=command.category_id
            )
            self._budgets.add(budget)
        else:
            budget.change_amount(amount)
        self._uow.commit()
        return BudgetView.of(budget)

    def clear(self, owner_id: OwnerId, category_id: CategoryId | None = None) -> None:
        budget = self._budgets.get(owner_id, category_id)
        self._budgets.remove(budget)
        self._uow.commit()
