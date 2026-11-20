"""Draft expenses, and the rules that turn a language model's answer into them.

A model is good at reading "ዛሬ ለታክሲ 150 ብር በቴሌብር" and bad at being trusted. So the model is asked
for a loose structure and everything that matters is decided here, deterministically:

* **Category** must be one of the Owner's live categories (the model is given their ids); anything
  else falls back to "Other" rather than being invented.
* **Amount** comes back in major units and is turned into integer minor units; zero, negative or
  absurd amounts are dropped.
* **Date** is never converted by the model. It says *how* the date was expressed — today, N days
  ago, or a written date in a named calendar — and the conversion happens here with the same
  calendar code as the rest of Kise. A date in the future becomes today; one more than a year back
  is kept but flagged.
* **Payment method** must be one Kise knows; otherwise cash.
* **Item line** survives only with a name, a positive quantity and a real unit.

Each draft carries ``warnings`` so the review screen can point at what to double-check.

Two more kinds of draft serve the Fixed Monthly Expenses:

* a **fixed draft** — "rent 8,000 every month on the 5th" — a new commitment, starting this month
  or next in the person's calendar;
* a **payment draft** — "I paid the rent" — marking one of the person's *existing* commitments
  paid for this month. The model may only point at a commitment by its id from the list it is
  shown; anything else is dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from kise.shared_kernel.domain.calendar.calendar_kind import CalendarKind
from kise.shared_kernel.domain.calendar.period import Period
from kise.shared_kernel.domain.calendar.spend_date import SpendDate
from kise.shared_kernel.domain.errors import DomainError

PAYMENT_METHODS: tuple[str, ...] = ("cash", "telebirr", "bank", "card", "other")
DATE_KINDS: tuple[str, ...] = ("today", "days_ago", "ethiopian", "gregorian")

#: The most drafts one request may produce — a long receipt, not a novel.
MAX_DRAFTS = 25
#: Above this many major units in one line, the reading is almost certainly wrong.
MAX_AMOUNT_MAJOR = Decimal("10000000")
MAX_NOTE_LENGTH = 280
MAX_ITEM_NAME_LENGTH = 60
#: As long as a category name may be (Expense Tracking's own limit).
MAX_CATEGORY_NAME_LENGTH = 60
#: How far back a date may be before it is flagged as suspicious.
STALE_AFTER_DAYS = 366


@dataclass(frozen=True, slots=True)
class CategoryOption:
    id: str
    name: str
    name_am: str | None = None


@dataclass(frozen=True, slots=True)
class UnitOption:
    id: str
    code: str
    name: str


@dataclass(frozen=True, slots=True)
class CommitmentOption:
    """One fixed monthly expense owed this month, as the model is shown it."""

    id: str
    title: str
    amount_minor: int
    due_day: int
    period: Period
    is_settled: bool = False


@dataclass(frozen=True, slots=True)
class Catalog:
    """What the Owner can file under: their live categories, the units of measurement, and the
    fixed monthly expenses owed in the current month."""

    categories: tuple[CategoryOption, ...]
    units: tuple[UnitOption, ...] = ()
    commitments: tuple[CommitmentOption, ...] = ()

    def commitment(self, commitment_id: str) -> CommitmentOption | None:
        return next((c for c in self.commitments if c.id == commitment_id), None)

    def category(self, category_id: str) -> CategoryOption | None:
        return next((c for c in self.categories if c.id == category_id), None)

    def category_named(self, name: str) -> CategoryOption | None:
        """The person's category with this name, in English or Amharic, ignoring case."""
        wanted = " ".join(name.split()).casefold()
        return next(
            (
                c
                for c in self.categories
                if c.name.casefold() == wanted or (c.name_am or "").casefold() == wanted
            ),
            None,
        )

    @property
    def fallback_category(self) -> CategoryOption | None:
        """"Other" when the Owner has it, else the first category."""
        for option in self.categories:
            if option.name.strip().casefold() == "other":
                return option
        return self.categories[0] if self.categories else None

    def unit(self, code: str) -> UnitOption | None:
        wanted = code.strip().casefold()
        return next((u for u in self.units if u.code.casefold() == wanted), None)


@dataclass(frozen=True, slots=True)
class DraftItem:
    item_name: str
    quantity: str
    unit_id: str
    unit_code: str


@dataclass(frozen=True, slots=True)
class DraftExpense:
    """An expense the person has not confirmed yet. Never stored."""

    category_id: str
    category_name: str
    category_name_am: str | None
    amount_minor: int
    spent_on: SpendDate
    payment_method: str
    note: str | None = None
    item: DraftItem | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    #: A category the model suggests creating because none of the person's fits. The draft is
    #: filed under ``category_id`` (the closest or Other) until the person accepts it.
    new_category: str | None = None


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _quantity_text(value: Decimal) -> str:
    """1.500 -> "1.5", 12 -> "12": what the expenses API accepts."""
    text = format(value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP).normalize(), "f")
    return text


def resolve_date(raw: dict[str, Any], today: date) -> tuple[date, list[str]]:
    """Turn the model's description of a date into a Gregorian date, plus any warnings."""
    warnings: list[str] = []
    kind = raw.get("date_kind")
    resolved = today
    if kind == "days_ago":
        days = raw.get("days_ago")
        if isinstance(days, int) and not isinstance(days, bool) and 0 <= days <= 3660:
            resolved = today - timedelta(days=days)
        else:
            warnings.append("date_unclear")
    elif kind in ("ethiopian", "gregorian"):
        text = _text(raw.get("date"), 10)
        try:
            calendar = CalendarKind.ETHIOPIAN if kind == "ethiopian" else CalendarKind.GREGORIAN
            resolved = SpendDate.parse(text, calendar).gregorian
        except (DomainError, ValueError):
            warnings.append("date_unclear")
            resolved = today
    elif kind != "today":
        warnings.append("date_unclear")

    if resolved > today:
        warnings.append("date_in_future")
        resolved = today
    elif (today - resolved).days > STALE_AFTER_DAYS:
        warnings.append("date_long_ago")
    return resolved, warnings


def _category(
    raw: dict[str, Any], catalog: Catalog
) -> tuple[CategoryOption | None, str | None, list[str]]:
    """The category to file under, a suggested new one, and warnings.

    A suggested name that matches one the person already has (in English or Amharic) is that
    category, not a new one. A real suggestion keeps the model's pick (or Other) as where the
    draft is filed until the person accepts the new category.
    """
    warnings: list[str] = []
    suggestion = _text(raw.get("new_category"), MAX_CATEGORY_NAME_LENGTH) or None
    if suggestion is not None:
        existing = catalog.category_named(suggestion)
        if existing is not None:
            return existing, None, warnings
    category = catalog.category(_text(raw.get("category_id"), 64))
    if category is None:
        category = catalog.fallback_category
        if suggestion is None:
            warnings.append("category_guessed")
    if suggestion is not None:
        warnings.append("new_category")
    return category, suggestion, warnings


def build_draft(raw: Any, catalog: Catalog, today: date) -> DraftExpense | None:
    """One draft from one entry of the model's ``expenses`` list, or None if it is unusable."""
    if not isinstance(raw, dict):
        return None
    amount = _decimal(raw.get("amount"))
    if amount is None or amount <= 0 or amount > MAX_AMOUNT_MAJOR:
        return None
    amount_minor = int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if amount_minor <= 0:
        return None

    category, new_category, warnings = _category(raw, catalog)
    if category is None:
        return None

    spent_on, date_warnings = resolve_date(raw, today)
    warnings.extend(date_warnings)

    method = _text(raw.get("payment_method"), 16).lower()
    if method not in PAYMENT_METHODS:
        method = "cash"

    item: DraftItem | None = None
    item_name = _text(raw.get("item_name"), MAX_ITEM_NAME_LENGTH)
    quantity = _decimal(raw.get("quantity"))
    unit = catalog.unit(_text(raw.get("unit_code"), 16)) if raw.get("unit_code") else None
    if item_name and quantity is not None and quantity > 0 and unit is not None:
        item = DraftItem(
            item_name=item_name,
            quantity=_quantity_text(quantity),
            unit_id=unit.id,
            unit_code=unit.code,
        )

    note = _text(raw.get("note"), MAX_NOTE_LENGTH) or None
    return DraftExpense(
        category_id=category.id,
        category_name=category.name,
        category_name_am=category.name_am,
        amount_minor=amount_minor,
        spent_on=SpendDate.from_gregorian(spent_on),
        payment_method=method,
        note=note,
        item=item,
        warnings=tuple(dict.fromkeys(warnings)),
        new_category=new_category,
    )


def build_drafts(raw_expenses: Any, catalog: Catalog, today: date) -> list[DraftExpense]:
    """Every usable draft from the model's ``expenses`` list, at most ``MAX_DRAFTS``."""
    if not isinstance(raw_expenses, list):
        return []
    drafts: list[DraftExpense] = []
    for raw in raw_expenses[:MAX_DRAFTS]:
        draft = build_draft(raw, catalog, today)
        if draft is not None:
            drafts.append(draft)
    return drafts


# -- fixed monthly expenses -------------------------------------------------

STARTS: tuple[str, ...] = ("this_month", "next_month")
MAX_TITLE_LENGTH = 80
MAX_DUE_DAY = 30
MAX_FIXED_DRAFTS = 10


@dataclass(frozen=True, slots=True)
class FixedDraft:
    """A monthly commitment the person described but has not confirmed."""

    title: str
    category_id: str
    category_name: str
    category_name_am: str | None
    amount_minor: int
    due_day: int
    start_period: Period
    note: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    #: As on ``DraftExpense``: a new category the model suggests.
    new_category: str | None = None


@dataclass(frozen=True, slots=True)
class PaymentDraft:
    """"I paid the rent": one existing commitment, marked paid for its current month."""

    fixed_expense_id: str
    title: str
    period: Period
    amount_minor: int
    paid_on: SpendDate
    warnings: tuple[str, ...] = field(default_factory=tuple)


def _amount_minor(value: Any) -> int | None:
    amount = _decimal(value)
    if amount is None or amount <= 0 or amount > MAX_AMOUNT_MAJOR:
        return None
    minor = int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return minor if minor > 0 else None


def build_fixed_draft(
    raw: Any, catalog: Catalog, today: date, calendar: CalendarKind
) -> FixedDraft | None:
    if not isinstance(raw, dict):
        return None
    title = _text(raw.get("title"), MAX_TITLE_LENGTH)
    amount_minor = _amount_minor(raw.get("amount"))
    if not title or amount_minor is None:
        return None

    category, new_category, warnings = _category(raw, catalog)
    if category is None:
        return None

    due_day = raw.get("due_day")
    if isinstance(due_day, bool) or not isinstance(due_day, int) or not 1 <= due_day <= 31:
        due_day = 1
        warnings.append("due_day_unclear")
    # Ethiopian months have 30 days; "the 31st" means the last day, which 30 already clamps to.
    due_day = min(due_day, MAX_DUE_DAY)

    start = Period.from_date(today, calendar)
    if raw.get("starts") == "next_month":
        start = start.next

    return FixedDraft(
        title=title,
        category_id=category.id,
        category_name=category.name,
        category_name_am=category.name_am,
        amount_minor=amount_minor,
        due_day=due_day,
        start_period=start,
        note=_text(raw.get("note"), MAX_NOTE_LENGTH) or None,
        warnings=tuple(dict.fromkeys(warnings)),
        new_category=new_category,
    )


def build_payment_draft(raw: Any, catalog: Catalog, today: date) -> PaymentDraft | None:
    if not isinstance(raw, dict):
        return None
    commitment = catalog.commitment(_text(raw.get("commitment_id"), 64))
    if commitment is None:
        return None
    warnings: list[str] = []
    if commitment.is_settled:
        warnings.append("already_paid")
    # A fixed monthly expense is paid at the cost set up on it. A different amount heard (often a
    # mishearing) is flagged so the person can check, never saved in its place.
    amount_minor = commitment.amount_minor
    heard = _amount_minor(raw.get("amount"))
    if heard is not None and heard != amount_minor:
        warnings.append("amount_differs")
    paid_on, date_warnings = resolve_date(raw, today)
    warnings.extend(date_warnings)
    return PaymentDraft(
        fixed_expense_id=commitment.id,
        title=commitment.title,
        period=commitment.period,
        amount_minor=amount_minor,
        paid_on=SpendDate.from_gregorian(paid_on),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def build_fixed_drafts(
    raw: Any, catalog: Catalog, today: date, calendar: CalendarKind
) -> list[FixedDraft]:
    if not isinstance(raw, list):
        return []
    drafts = [build_fixed_draft(r, catalog, today, calendar) for r in raw[:MAX_FIXED_DRAFTS]]
    return [d for d in drafts if d is not None]


def build_payment_drafts(raw: Any, catalog: Catalog, today: date) -> list[PaymentDraft]:
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    drafts: list[PaymentDraft] = []
    for entry in raw[:MAX_FIXED_DRAFTS]:
        draft = build_payment_draft(entry, catalog, today)
        # One payment per commitment per month: saying "paid rent" twice is still one payment.
        if draft is not None and draft.fixed_expense_id not in seen:
            seen.add(draft.fixed_expense_id)
            drafts.append(draft)
    return drafts
