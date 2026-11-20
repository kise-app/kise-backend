"""Monthly allowances in the Assistant domain: months, limits, what is left."""

from __future__ import annotations

from datetime import date

from kise.assistant.domain.usage import (
    Allowance,
    UsageKind,
    UsageSummary,
    month_of,
    next_month_start,
)


def test_a_month_is_a_calendar_month():
    assert month_of(date(2026, 9, 4)) == "2026-09"
    assert next_month_start(date(2026, 9, 30)) == date(2026, 10, 1)
    assert next_month_start(date(2026, 12, 15)) == date(2027, 1, 1)


def test_an_allowance_of_none_means_no_limit_and_zero_means_not_included():
    unlimited = Allowance()
    assert unlimited.includes(UsageKind.RECEIPT) and unlimited.has_room(UsageKind.RECEIPT, 10**6)
    none = Allowance(receipts=0, voice=0)
    assert not none.includes(UsageKind.VOICE)
    some = Allowance(receipts=2, voice=5)
    assert some.has_room(UsageKind.RECEIPT, 1) and not some.has_room(UsageKind.RECEIPT, 2)
    assert some.limit(UsageKind.VOICE) == 5


def test_remaining_never_goes_below_zero():
    assert UsageSummary(UsageKind.RECEIPT, limit=3, used=5).remaining == 0
    assert UsageSummary(UsageKind.VOICE, limit=None, used=5).remaining is None
