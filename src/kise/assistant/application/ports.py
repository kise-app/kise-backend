"""What the Assistant needs from the outside world, as ports.

``LanguageModel``    reads text, audio or an image and answers in JSON (or plain text). Gemini is
                     the adapter today; another provider is one class in ``infrastructure/``.
``SpendingContext``  the Owner's categories and units, and facts about their months. Implemented
                     in the composition root over the Expense Tracking services, so this context
                     never imports another one.
``RateLimiter``      an allowance per Owner, because every call costs money.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from kise.assistant.domain.drafts import Catalog
from kise.shared_kernel.domain.calendar.calendar_kind import CalendarKind
from kise.shared_kernel.domain.identifiers import OwnerId


@dataclass(frozen=True, slots=True)
class TextPart:
    text: str


@dataclass(frozen=True, slots=True)
class MediaPart:
    """Raw bytes and their IANA type: ``audio/mp4``, ``image/jpeg`` ..."""

    mime_type: str
    data: bytes


Part = TextPart | MediaPart


class LanguageModel(ABC):
    @abstractmethod
    def generate_json(
        self, *, system: str, parts: list[Part], schema: dict[str, Any]
    ) -> dict[str, Any]:
        """Answer with a JSON object that follows ``schema``. Raises ``AssistantFailed``."""

    @abstractmethod
    def generate_text(self, *, system: str, parts: list[Part]) -> str:
        """Answer in plain text. Raises ``AssistantFailed``."""


class SpendingContext(Protocol):
    def catalog(self, owner_id: OwnerId, calendar: CalendarKind, today: date) -> Catalog:
        """The Owner's live categories, every unit of measurement, and the fixed monthly
        expenses owed in the month containing ``today``."""
        ...

    def month_facts(
        self, owner_id: OwnerId, calendar: CalendarKind, today: date, months: int
    ) -> list[dict[str, Any]]:
        """Totals for the month containing ``today`` and the ``months - 1`` before it."""
        ...


class RateLimiter(ABC):
    @abstractmethod
    def take(self, key: str) -> None:
        """Use one request of ``key``'s allowance, or raise ``AssistantRateLimited``."""
