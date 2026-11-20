"""Monthly allowances: how many receipt scans and voice notes an account may send.

The plan says how many (``Allowance``); the usage meter says how many were used. A month is a
calendar month in UTC, so every count starts again on the 1st.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


class UsageKind(str, Enum):
    """What counts against an allowance. Voice covers recording expenses and asking aloud."""

    RECEIPT = "receipt"
    VOICE = "voice"


def month_of(day: date) -> str:
    """The period a request on ``day`` counts towards, e.g. ``2026-11``."""
    return f"{day.year:04d}-{day.month:02d}"


def next_month_start(day: date) -> date:
    """When the counts start again."""
    return date(day.year + 1, 1, 1) if day.month == 12 else date(day.year, day.month + 1, 1)


@dataclass(frozen=True, slots=True)
class Allowance:
    """How many of each kind the plan in force includes a month. None is no monthly limit."""

    receipts: int | None = None
    voice: int | None = None

    def limit(self, kind: UsageKind) -> int | None:
        return self.receipts if kind is UsageKind.RECEIPT else self.voice

    def includes(self, kind: UsageKind) -> bool:
        """False when the plan has none of ``kind`` at all (as opposed to having used them up)."""
        return self.limit(kind) != 0

    def has_room(self, kind: UsageKind, used: int) -> bool:
        limit = self.limit(kind)
        return limit is None or used < limit


@dataclass(frozen=True, slots=True)
class UsageSummary:
    """One kind's allowance this month, for the app to show "12 of 30 left"."""

    kind: UsageKind
    limit: int | None
    used: int

    @property
    def remaining(self) -> int | None:
        return None if self.limit is None else max(0, self.limit - self.used)
