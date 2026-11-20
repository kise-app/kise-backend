"""The instructions and the answer schema the language model is given.

Kept apart from the use cases so they can be read — and tuned — on their own. Everything the model
must not decide is left out of its job on purpose: it names a category by *id* from the list it is
shown, gives amounts in major units, and describes a date instead of converting it.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from kise.assistant.domain.drafts import DATE_KINDS, PAYMENT_METHODS, STARTS, Catalog
from kise.shared_kernel.domain.calendar.calendar_kind import CalendarKind
from kise.shared_kernel.domain.calendar.spend_date import SpendDate

LANGUAGE_NAMES = {"am": "Amharic", "en": "English"}


def _today_line(today: date) -> str:
    spend = SpendDate.from_gregorian(today)
    et = spend.ethiopian
    return (
        f"Today is {today.isoformat()} (Gregorian), which is Ethiopian {et.iso} "
        f"({et.month_name_en} {et.day}, {et.year}; {et.month_name_am})."
    )


def extraction_schema(catalog: Catalog) -> dict[str, Any]:
    category_ids = [c.id for c in catalog.categories]
    unit_codes = [u.code for u in catalog.units] + [""]
    expense = {
        "type": "object",
        "properties": {
            "amount": {
                "type": "number",
                "description": "What was paid for this line, in major currency units (birr, not "
                "santim). A receipt line's total, not its unit price.",
            },
            "category_id": {"type": "string", "enum": category_ids},
            "new_category": {
                "type": "string",
                "description": "Only when no category in the list fits at all: a short name "
                "for a new one, in the person's language (Rent, ኪራይ, School). Empty otherwise.",
            },
            "date_kind": {
                "type": "string",
                "enum": list(DATE_KINDS),
                "description": "today; days_ago (use days_ago); or a date written out, in the "
                "calendar it was written in (put it in date).",
            },
            "days_ago": {"type": "integer", "description": "1 for yesterday. 0 otherwise."},
            "date": {
                "type": "string",
                "description": "YYYY-MM-DD in the calendar named by date_kind. Do not convert "
                "between calendars. Empty unless date_kind is ethiopian or gregorian.",
            },
            "payment_method": {"type": "string", "enum": list(PAYMENT_METHODS)},
            "note": {
                "type": "string",
                "description": "A few words: the merchant, the place, or what it was for.",
            },
            "item_name": {
                "type": "string",
                "description": "Only for goods counted in a unit (sugar, oil, teff). Else empty.",
            },
            "quantity": {"type": "number", "description": "How much of the item. 0 if none."},
            "unit_code": {"type": "string", "enum": unit_codes},
        },
        "required": ["amount", "category_id", "date_kind", "payment_method"],
    }
    commitment_ids = [c.id for c in catalog.commitments] or [""]
    fixed = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short name: Rent, Internet, School fees."},
            "amount": {"type": "number", "description": "The monthly amount, in major units."},
            "category_id": {"type": "string", "enum": category_ids},
            "new_category": {
                "type": "string",
                "description": "Only when no category in the list fits at all: a short name "
                "for a new one, in the person's language (Rent, ኪራይ, School). Empty otherwise.",
            },
            "due_day": {
                "type": "integer",
                "description": "Day of the month it is due, 1-30. 1 when not said.",
            },
            "starts": {
                "type": "string",
                "enum": list(STARTS),
                "description": "this_month unless they said it starts next month.",
            },
            "note": {"type": "string"},
        },
        "required": ["title", "amount", "category_id", "due_day", "starts"],
    }
    payment = {
        "type": "object",
        "properties": {
            "commitment_id": {
                "type": "string",
                "enum": commitment_ids,
                "description": "Which of the person's fixed monthly expenses was paid.",
            },
            "amount": {
                "type": "number",
                "description": "What was paid, in major units. 0 if they did not say.",
            },
            "date_kind": {"type": "string", "enum": list(DATE_KINDS)},
            "days_ago": {"type": "integer"},
            "date": {"type": "string"},
        },
        "required": ["commitment_id", "amount", "date_kind"],
    }
    return {
        "type": "object",
        "properties": {
            "transcript": {
                "type": "string",
                "description": "For audio: what was said, word for word, in the language it was "
                "said in. Empty for text and images.",
            },
            "message": {
                "type": "string",
                "description": "One short sentence for the person, in their language: what was "
                "found, or what is missing (for example no amount was said).",
            },
            "merchant": {"type": "string", "description": "For a receipt: the shop name."},
            "expenses": {"type": "array", "items": expense},
            "fixed_expenses": {
                "type": "array",
                "items": fixed,
                "description": "New monthly commitments they described (rent every month ...).",
            },
            "payments": {
                "type": "array",
                "items": payment,
                "description": "Existing fixed monthly expenses they say they paid.",
            },
        },
        "required": ["message", "expenses"],
    }


def extraction_instructions(
    *,
    catalog: Catalog,
    today: date,
    language: str,
    calendar: CalendarKind,
    currency: str,
    source: str,
) -> str:
    categories = [
        {"id": c.id, "name": c.name, "name_am": c.name_am} for c in catalog.categories
    ]
    units = [{"code": u.code, "name": u.name} for u in catalog.units]
    commitments = [
        {
            "id": c.id,
            "title": c.title,
            "amount": f"{c.amount_minor / 100:.2f}",
            "due_day": c.due_day,
            "already_paid_this_month": c.is_settled,
        }
        for c in catalog.commitments
    ]
    source_rules = {
        "text": "The input is a short message the person typed.",
        "voice": "The input is a voice recording, usually Amharic, English, or a mix of both. "
        "First transcribe it exactly into transcript.",
        "receipt": "The input is a photo of a receipt. Read the shop name, the date printed on "
        "it, and either one expense for the grand total or one per line when the lines are "
        "clearly different kinds of purchase. Prefer the grand total for a single shop visit. "
        "Ignore VAT, change and subtotals as separate lines.",
    }[source]
    return "\n".join(
        [
            "You turn what a person in Ethiopia spent into structured expenses for the Kise app.",
            source_rules,
            _today_line(today),
            f"The person reads {LANGUAGE_NAMES.get(language, 'English')} and uses the "
            f"{calendar.value} calendar. Their currency is {currency}.",
            "Rules:",
            "- One entry per distinct thing paid for. 'bread 40, coffee 30' is two entries.",
            "- Amounts are in major units: '150 ብር' is 150. 'ሺ' or 'k' means thousand.",
            "- Pick category_id from the list below; use the closest match, or the Other "
            "category when nothing fits. When none of them fits at all, also put a short name "
            "for a new category in new_category (the person can accept it); leave it empty "
            "whenever a listed category will do.",
            "- Dates: 'ዛሬ'/'today' -> today; 'ትናንት'/'yesterday' -> days_ago 1. A written date "
            "goes in date in the calendar it was written in, with date_kind saying which. "
            "Ethiopian years are around 2018-2019; Gregorian around 2025-2027. Do not convert "
            "calendars.",
            "- Payment: ቴሌብር/telebirr -> telebirr; ባንክ, CBE, transfer -> bank; card -> card; "
            "otherwise cash.",
            "- A cost that repeats every month (ቤት ኪራይ/rent, internet, school fees, 'every "
            "month', 'በየወሩ') is a fixed_expenses entry, not an expense.",
            "- 'I paid the rent' / 'ኪራይ ከፈልኩ' for one of the person's fixed monthly expenses "
            "listed below is a payments entry with that commitment_id, not an expense. Only use "
            "ids from that list.",
            "- If no amount can be found, return no expenses and say so in message.",
            "- Never invent expenses that were not said or shown.",
            f"Categories: {json.dumps(categories, ensure_ascii=False)}",
            f"Units: {json.dumps(units, ensure_ascii=False)}",
            "Fixed monthly expenses owed this month: "
            f"{json.dumps(commitments, ensure_ascii=False)}",
        ]
    )


def question_instructions(
    *, facts: list[dict[str, Any]], today: date, language: str, currency: str
) -> str:
    return "\n".join(
        [
            "You answer questions about the person's own spending in the Kise app.",
            _today_line(today),
            f"Answer in {LANGUAGE_NAMES.get(language, 'English')}, in at most three short "
            f"sentences. Amounts are in {currency}; write them like 1,250 {currency}.",
            "Use only the facts below. They are exact; do not estimate beyond them. If the facts "
            "cannot answer the question, say what you can answer instead.",
            "Each month's grand_total = dynamic_total (day-to-day expenses) + committed (fixed "
            "monthly payments). Months are named in the person's calendar.",
            "Do not give financial advice beyond simple observations about these numbers.",
            f"Facts: {json.dumps(facts, ensure_ascii=False)}",
        ]
    )


def question_schema() -> dict[str, Any]:
    """For a spoken question: what was asked, and the answer."""
    return {
        "type": "object",
        "properties": {
            "transcript": {
                "type": "string",
                "description": "The question as it was said, word for word, in its language.",
            },
            "answer": {"type": "string", "description": "The answer, following the rules."},
        },
        "required": ["transcript", "answer"],
    }
