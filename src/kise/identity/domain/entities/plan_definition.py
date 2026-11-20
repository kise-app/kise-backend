"""PlanDefinition — one plan Kise offers: what it is called, costs and includes.

Admins add plans, change them, hide them from the app and remove them. Two are built in because
the rules depend on them, so they can be changed but never removed: Trial (where new accounts
start; it has a length, not a price) and Basic (where every plan falls back to when it ends).
A plan that still has accounts on it cannot be removed either, only hidden: removing it would leave
those accounts on a plan that does not exist.
"""

from __future__ import annotations

from datetime import datetime

from kise.identity.domain.value_objects import (
    BASIC,
    BUILT_IN_PLANS,
    PRO,
    TRIAL,
    PlanAllowance,
    PlanCode,
)
from kise.shared_kernel.domain.entity import AggregateRoot
from kise.shared_kernel.domain.errors import InvariantViolation, ValidationError
from kise.shared_kernel.domain.identifiers import PlanId

MAX_PLAN_NAME_LENGTH = 40
MAX_PLAN_PRICE_LENGTH = 120


def _name(raw: str) -> str:
    cleaned = " ".join(str(raw or "").split())
    if not cleaned:
        raise ValidationError("A plan needs a name", field="name")
    if len(cleaned) > MAX_PLAN_NAME_LENGTH:
        raise ValidationError(
            f"Keep the name under {MAX_PLAN_NAME_LENGTH} characters", field="name"
        )
    return cleaned


def _price(raw: str) -> str:
    cleaned = " ".join(str(raw or "").split())
    if len(cleaned) > MAX_PLAN_PRICE_LENGTH:
        raise ValidationError(
            f"Keep the price under {MAX_PLAN_PRICE_LENGTH} characters", field="price"
        )
    return cleaned


class PlanDefinition(AggregateRoot):
    def __init__(
        self,
        plan_id: PlanId,
        *,
        code: PlanCode,
        name: str,
        price: str,
        typed_requests: bool,
        allowance: PlanAllowance,
        position: int,
        visible: bool,
        created_at: datetime,
    ) -> None:
        super().__init__(plan_id)
        if not isinstance(allowance, PlanAllowance):
            raise ValidationError("allowance must be a PlanAllowance", field="allowance")
        self._code = PlanCode.parse(code)
        self._name = _name(name)
        self._price = "" if self._code == TRIAL else _price(price)
        self._typed_requests = bool(typed_requests)
        self._allowance = allowance
        self._position = int(position)
        # The trial is never offered in the app: new accounts are put on it, nobody buys it.
        self._visible = bool(visible) and self._code != TRIAL
        self._created_at = created_at

    @classmethod
    def create(
        cls,
        *,
        code: PlanCode,
        name: str,
        price: str,
        typed_requests: bool,
        allowance: PlanAllowance,
        position: int,
        now: datetime,
        visible: bool = True,
    ) -> PlanDefinition:
        return cls(
            PlanId.new(),
            code=code,
            name=name,
            price=price,
            typed_requests=typed_requests,
            allowance=allowance,
            position=position,
            visible=visible,
            created_at=now,
        )

    # -- state ----------------------------------------------------------
    @property
    def id(self) -> PlanId:
        return self._id  # type: ignore[return-value]

    @property
    def code(self) -> PlanCode:
        return self._code

    @property
    def name(self) -> str:
        return self._name

    @property
    def price(self) -> str:
        """Free text, as the admin wrote it ("300 birr a month"). Always empty for the trial."""
        return self._price

    @property
    def typed_requests(self) -> bool:
        """Typed requests to the assistant (text and typed questions)."""
        return self._typed_requests

    @property
    def allowance(self) -> PlanAllowance:
        return self._allowance

    @property
    def includes_assistant(self) -> bool:
        """Any part of the assistant at all: typed requests, scans or voice."""
        a = self._allowance
        return self._typed_requests or a.receipts != 0 or a.voice != 0

    @property
    def position(self) -> int:
        return self._position

    @property
    def visible(self) -> bool:
        """Offered in the app's upgrade sheet."""
        return self._visible

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def built_in(self) -> bool:
        return self._code in BUILT_IN_PLANS

    @property
    def is_trial(self) -> bool:
        return self._code == TRIAL

    @property
    def is_fallback(self) -> bool:
        return self._code == BASIC

    # -- behaviour ------------------------------------------------------
    def change(
        self,
        *,
        name: str,
        price: str,
        typed_requests: bool,
        allowance: PlanAllowance,
        visible: bool,
    ) -> None:
        if not isinstance(allowance, PlanAllowance):
            raise ValidationError("allowance must be a PlanAllowance", field="allowance")
        self._name = _name(name)
        self._price = "" if self.is_trial else _price(price)
        self._typed_requests = bool(typed_requests)
        self._allowance = allowance
        self._visible = bool(visible) and not self.is_trial

    def move_to(self, position: int) -> None:
        self._position = int(position)

    def ensure_removable(self, accounts_on_it: int) -> None:
        if self.built_in:
            raise InvariantViolation(f"{self._name} is built in: change it instead of removing it")
        if accounts_on_it:
            noun = "account is" if accounts_on_it == 1 else "accounts are"
            raise InvariantViolation(
                f"{accounts_on_it} {noun} on {self._name}. Hide it from the app instead, or move "
                "those accounts to another plan first."
            )


def default_plans(now: datetime) -> list[PlanDefinition]:
    """The plans a new installation starts with: what Kise offered before plans could be added.
    Trial and Pro include everything without a monthly limit; Basic everything but the assistant."""
    everything = PlanAllowance()
    return [
        PlanDefinition.create(
            code=TRIAL,
            name="Trial",
            price="",
            typed_requests=True,
            allowance=everything,
            position=0,
            now=now,
            visible=False,
        ),
        PlanDefinition.create(
            code=BASIC,
            name="Basic",
            price="",
            typed_requests=False,
            allowance=PlanAllowance(receipts=0, voice=0),
            position=10,
            now=now,
        ),
        PlanDefinition.create(
            code=PRO,
            name="Pro",
            price="",
            typed_requests=True,
            allowance=everything,
            position=20,
            now=now,
        ),
    ]
