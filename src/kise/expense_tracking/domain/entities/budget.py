"""The Budget aggregate — a monthly spending limit.

Two kinds, distinguished only by ``category_id``:

* the **overall** budget (``category_id is None``): what the Owner means to spend in a month, fixed
  commitments included — the dashboard's ring;
* a **category** budget: a limit on one Category ("Food: 6,000 a month"), which is what turns the
  category breakdown into a warning when a month runs hot.

A budget applies to every month in whichever calendar is being viewed; it is a number per month,
not a schedule. "At most one budget per (Owner, category)" spans aggregates, so the service enforces
it by looking up before creating.
"""

from __future__ import annotations

from kise.shared_kernel.domain.entity import AggregateRoot
from kise.shared_kernel.domain.errors import InvariantViolation
from kise.shared_kernel.domain.identifiers import BudgetId, CategoryId, OwnerId
from kise.shared_kernel.domain.money import Money


class Budget(AggregateRoot):
    def __init__(
        self,
        budget_id: BudgetId,
        *,
        owner_id: OwnerId,
        amount: Money,
        category_id: CategoryId | None = None,
    ) -> None:
        super().__init__(budget_id)
        self._owner_id = owner_id
        self._category_id = category_id
        self._amount = self._checked(amount)

    @staticmethod
    def _checked(amount: Money) -> Money:
        if not isinstance(amount, Money) or not amount.is_positive:
            raise InvariantViolation("A budget must be greater than zero")
        return amount

    @classmethod
    def set(
        cls,
        *,
        owner_id: OwnerId,
        amount: Money,
        category_id: CategoryId | None = None,
        budget_id: BudgetId | None = None,
    ) -> Budget:
        return cls(
            budget_id or BudgetId.new(), owner_id=owner_id, amount=amount, category_id=category_id
        )

    @property
    def id(self) -> BudgetId:
        return self._id  # type: ignore[return-value]

    @property
    def owner_id(self) -> OwnerId:
        return self._owner_id

    @property
    def category_id(self) -> CategoryId | None:
        return self._category_id

    @property
    def is_overall(self) -> bool:
        return self._category_id is None

    @property
    def amount(self) -> Money:
        return self._amount

    def change_amount(self, amount: Money) -> None:
        if amount.currency != self._amount.currency:
            raise InvariantViolation(
                "A budget cannot change currency", was=self._amount.currency, given=amount.currency
            )
        self._amount = self._checked(amount)
