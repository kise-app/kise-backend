"""The rules that turn a language model's answer into draft expenses.

The model is not trusted with anything that matters, so these are the tests that matter: amounts,
categories, dates in both calendars, payment methods and item lines are all decided here.
"""

from __future__ import annotations

from datetime import date

import pytest

from kise.assistant.domain.drafts import (
    MAX_DRAFTS,
    Catalog,
    CategoryOption,
    CommitmentOption,
    UnitOption,
    build_draft,
    build_drafts,
    build_fixed_draft,
    build_payment_drafts,
    resolve_date,
)
from kise.shared_kernel.domain.calendar.calendar_kind import CalendarKind
from kise.shared_kernel.domain.calendar.period import Period

TODAY = date(2026, 9, 4)  # ነሐሴ 29, 2018

CATALOG = Catalog(
    categories=(
        CategoryOption("c-food", "Food", "ምግብ"),
        CategoryOption("c-taxi", "Transport", "ትራንስፖርት"),
        CategoryOption("c-other", "Other", "ሌላ"),
    ),
    units=(UnitOption("u-kg", "kg", "Kilogram"), UnitOption("u-l", "l", "Litre")),
)


def line(**overrides) -> dict:
    return {
        "amount": 150,
        "category_id": "c-taxi",
        "date_kind": "today",
        "payment_method": "telebirr",
        **overrides,
    }


def test_a_plain_line():
    draft = build_draft(line(note="  Taxi   to Bole "), CATALOG, TODAY)
    assert draft is not None
    assert draft.amount_minor == 15000
    assert draft.category_id == "c-taxi"
    assert draft.category_name_am == "ትራንስፖርት"
    assert draft.spent_on.gregorian == TODAY
    assert draft.payment_method == "telebirr"
    assert draft.note == "Taxi to Bole"
    assert draft.item is None
    assert draft.warnings == ()


@pytest.mark.parametrize(
    ("amount", "minor"),
    [(150, 15000), ("40.5", 4050), (0.015, 2), (1250.999, 125100)],
)
def test_amounts_become_integer_minor_units(amount, minor):
    assert build_draft(line(amount=amount), CATALOG, TODAY).amount_minor == minor


@pytest.mark.parametrize("amount", [0, -5, "abc", None, True, 10_000_001, float("nan"), 0.001])
def test_unusable_amounts_drop_the_line(amount):
    assert build_draft(line(amount=amount), CATALOG, TODAY) is None


def test_an_unknown_category_falls_back_to_other_and_says_so():
    draft = build_draft(line(category_id="invented"), CATALOG, TODAY)
    assert draft.category_id == "c-other"
    assert "category_guessed" in draft.warnings


def test_without_other_the_first_category_is_the_fallback():
    catalog = Catalog(categories=(CategoryOption("c-food", "Food"),))
    assert build_draft(line(category_id="nope"), catalog, TODAY).category_id == "c-food"


def test_no_categories_means_no_drafts():
    assert build_draft(line(), Catalog(categories=()), TODAY) is None


def test_unknown_payment_method_is_cash():
    assert build_draft(line(payment_method="bitcoin"), CATALOG, TODAY).payment_method == "cash"


# -- dates ----------------------------------------------------------------


def test_yesterday():
    resolved, warnings = resolve_date({"date_kind": "days_ago", "days_ago": 1}, TODAY)
    assert resolved == date(2026, 9, 3)
    assert warnings == []


def test_an_ethiopian_date_is_converted_here_not_by_the_model():
    # ሐምሌ 3, 2018 is 10 July 2026.
    resolved, warnings = resolve_date({"date_kind": "ethiopian", "date": "2018-11-03"}, TODAY)
    assert resolved == date(2026, 7, 10)
    assert warnings == []


def test_pagumen_is_understood():
    # ጳጉሜን 1, 2017 is 6 September 2025.
    resolved, _ = resolve_date({"date_kind": "ethiopian", "date": "2017-13-01"}, TODAY)
    assert resolved == date(2025, 9, 6)


def test_a_gregorian_date():
    resolved, _ = resolve_date({"date_kind": "gregorian", "date": "2026-08-30"}, TODAY)
    assert resolved == date(2026, 8, 30)


@pytest.mark.parametrize(
    "raw",
    [
        {"date_kind": "ethiopian", "date": "2018-13-09"},  # ጳጉሜን has 5 days in 2018
        {"date_kind": "gregorian", "date": "next tuesday"},
        {"date_kind": "days_ago", "days_ago": -3},
        {"date_kind": "days_ago"},
        {"date_kind": "sometime"},
    ],
)
def test_an_unreadable_date_becomes_today_with_a_warning(raw):
    resolved, warnings = resolve_date(raw, TODAY)
    assert resolved == TODAY
    assert "date_unclear" in warnings


def test_a_future_date_becomes_today():
    resolved, warnings = resolve_date({"date_kind": "gregorian", "date": "2026-12-25"}, TODAY)
    assert resolved == TODAY
    assert warnings == ["date_in_future"]


def test_a_date_more_than_a_year_back_is_kept_but_flagged():
    resolved, warnings = resolve_date({"date_kind": "gregorian", "date": "2024-01-01"}, TODAY)
    assert resolved == date(2024, 1, 1)
    assert warnings == ["date_long_ago"]


# -- item lines -------------------------------------------------------------


def test_an_item_line_needs_name_quantity_and_a_real_unit():
    draft = build_draft(
        line(category_id="c-food", item_name="Sugar", quantity=1.5, unit_code="KG"),
        CATALOG,
        TODAY,
    )
    assert draft.item is not None
    item = draft.item
    assert (item.item_name, item.quantity, item.unit_id) == ("Sugar", "1.5", "u-kg")


@pytest.mark.parametrize(
    "extra",
    [
        {"item_name": "Sugar", "quantity": 2, "unit_code": "sack"},  # not a unit we have
        {"item_name": "Sugar", "quantity": 0, "unit_code": "kg"},
        {"item_name": "", "quantity": 2, "unit_code": "kg"},
        {"item_name": "Sugar", "quantity": 2, "unit_code": ""},
    ],
)
def test_an_incomplete_item_line_is_dropped_but_the_expense_kept(extra):
    draft = build_draft(line(**extra), CATALOG, TODAY)
    assert draft is not None
    assert draft.item is None


# -- lists ------------------------------------------------------------------


def test_build_drafts_skips_garbage_and_caps_the_count():
    raw = [line(), "nonsense", {"amount": "free"}, *[line(amount=i + 1) for i in range(40)]]
    drafts = build_drafts(raw, CATALOG, TODAY)
    assert len(drafts) == MAX_DRAFTS - 2  # the two bad entries still count toward the cap


def test_build_drafts_of_not_a_list_is_empty():
    assert build_drafts({"amount": 5}, CATALOG, TODAY) == []



# -- fixed monthly expenses -----------------------------------------------------

NEHASE = Period(CalendarKind.ETHIOPIAN, 2018, 12)
WITH_RENT = Catalog(
    categories=CATALOG.categories,
    commitments=(CommitmentOption("fx-1", "Rent", 800000, 5, NEHASE),),
)


def fixed(**overrides) -> dict:
    return {
        "title": "Rent",
        "amount": 8000,
        "category_id": "c-other",
        "due_day": 5,
        "starts": "this_month",
        **overrides,
    }


def test_a_fixed_draft_starts_this_month_in_the_persons_calendar():
    draft = build_fixed_draft(fixed(), CATALOG, TODAY, CalendarKind.ETHIOPIAN)
    assert draft.amount_minor == 800000
    assert draft.start_period == NEHASE
    gregorian = build_fixed_draft(
        fixed(starts="next_month"), CATALOG, TODAY, CalendarKind.GREGORIAN
    )
    assert gregorian.start_period == Period(CalendarKind.GREGORIAN, 2026, 10)


@pytest.mark.parametrize(("due_day", "expected", "warned"), [(31, 30, False), (0, 1, True)])
def test_due_days_are_kept_within_a_month(due_day, expected, warned):
    draft = build_fixed_draft(fixed(due_day=due_day), CATALOG, TODAY, CalendarKind.ETHIOPIAN)
    assert draft.due_day == expected
    assert ("due_day_unclear" in draft.warnings) is warned


@pytest.mark.parametrize("bad", [{"title": ""}, {"amount": 0}, {"amount": "lots"}])
def test_a_fixed_draft_needs_a_title_and_an_amount(bad):
    assert build_fixed_draft(fixed(**bad), CATALOG, TODAY, CalendarKind.ETHIOPIAN) is None


def test_a_payment_must_name_a_real_commitment_and_defaults_to_its_amount():
    drafts = build_payment_drafts(
        [
            {"commitment_id": "fx-1", "amount": 0, "date_kind": "days_ago", "days_ago": 2},
            {"commitment_id": "fx-1", "amount": 7500, "date_kind": "today"},  # duplicate
            {"commitment_id": "made-up", "amount": 100, "date_kind": "today"},
        ],
        WITH_RENT,
        TODAY,
    )
    (payment,) = drafts
    assert payment.fixed_expense_id == "fx-1"
    assert payment.amount_minor == 800000
    assert payment.period == NEHASE
    assert payment.paid_on.gregorian == date(2026, 9, 2)


def test_paying_an_already_paid_month_is_flagged():
    paid = Catalog(
        categories=CATALOG.categories,
        commitments=(CommitmentOption("fx-1", "Rent", 800000, 5, NEHASE, is_settled=True),),
    )
    (payment,) = build_payment_drafts(
        [{"commitment_id": "fx-1", "amount": 0, "date_kind": "today"}], paid, TODAY
    )
    assert "already_paid" in payment.warnings
