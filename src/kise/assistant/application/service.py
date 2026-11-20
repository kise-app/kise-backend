"""The Assistant use cases: read text, a voice note or a receipt into drafts; answer a question.

Every call goes the same way: take one unit of the Owner's hourly allowance, check the input,
build the instructions from the Owner's real categories and units, ask the model, and pass whatever
comes back through ``build_drafts`` — which is where trust is decided. Nothing is written: the
person confirms drafts and the app records them through the expenses API.

Audio and images are sent to the model and then dropped; they are never stored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from kise.assistant.application.ports import (
    LanguageModel,
    MediaPart,
    Part,
    RateLimiter,
    SpendingContext,
    TextPart,
    UsageRepository,
)
from kise.assistant.application.prompts import (
    extraction_instructions,
    extraction_schema,
    question_instructions,
    question_schema,
)
from kise.assistant.domain.drafts import (
    DraftExpense,
    FixedDraft,
    PaymentDraft,
    build_drafts,
    build_fixed_drafts,
    build_payment_drafts,
)
from kise.assistant.domain.errors import (
    AllowanceUsedUp,
    AssistantFailed,
    MediaTooLarge,
    UnsupportedMedia,
    UpgradeRequired,
)
from kise.assistant.domain.usage import (
    Allowance,
    UsageKind,
    UsageSummary,
    month_of,
    next_month_start,
)
from kise.shared_kernel.application.ports import Clock, UnitOfWork
from kise.shared_kernel.domain.calendar.calendar_kind import CalendarKind
from kise.shared_kernel.domain.errors import ValidationError
from kise.shared_kernel.domain.identifiers import OwnerId

MAX_TEXT_LENGTH = 1000
MAX_QUESTION_LENGTH = 500
MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_ANSWER_LENGTH = 1200

#: What the app records (AAC in an MP4 container) plus the common alternatives. ``audio/x-m4a`` and
#: friends are normalised to the type the model expects.
AUDIO_TYPES: dict[str, str] = {
    "audio/mp4": "audio/mp4",
    "audio/m4a": "audio/mp4",
    "audio/x-m4a": "audio/mp4",
    "audio/aac": "audio/aac",
    "audio/mpeg": "audio/mpeg",
    "audio/mp3": "audio/mpeg",
    "audio/wav": "audio/wav",
    "audio/x-wav": "audio/wav",
    "audio/wave": "audio/wav",
    "audio/ogg": "audio/ogg",
    "audio/webm": "audio/webm",
    "audio/flac": "audio/flac",
}
IMAGE_TYPES: dict[str, str] = {
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "image/png": "image/png",
    "image/webp": "image/webp",
    "image/heic": "image/heic",
    "image/heif": "image/heif",
}


@dataclass(frozen=True, slots=True)
class AssistantRequest:
    """Who is asking and how they read the app. ``today`` is the client's date, checked here."""

    owner_id: OwnerId
    language: str = "am"
    calendar: CalendarKind = CalendarKind.ETHIOPIAN
    currency: str = "ETB"
    client_today: date | None = None
    #: Whether the owner's plan includes typed requests (Pro, or a running trial).
    allowed: bool = True
    #: Receipt scans and voice notes the plan in force includes a month.
    allowance: Allowance = field(default_factory=Allowance)


@dataclass(frozen=True, slots=True)
class DraftReply:
    drafts: tuple[DraftExpense, ...]
    message: str
    transcript: str | None = None
    merchant: str | None = None
    fixed_drafts: tuple[FixedDraft, ...] = ()
    payment_drafts: tuple[PaymentDraft, ...] = ()


@dataclass(frozen=True, slots=True)
class AnswerReply:
    answer: str
    facts: list[dict[str, Any]] = field(default_factory=list)
    #: For a spoken question: what was heard.
    transcript: str | None = None


def _media_type(content_type: str | None) -> str:
    return (content_type or "").split(";")[0].strip().lower()


class AssistantService:
    def __init__(
        self,
        model: LanguageModel,
        spending: SpendingContext,
        limiter: RateLimiter,
        clock: Clock,
        usage: UsageRepository | None = None,
        uow: UnitOfWork | None = None,
    ) -> None:
        # Both or neither: without them (tests, a server with no database) nothing is counted.
        self._usage = usage
        self._uow = uow
        self._model = model
        self._spending = spending
        self._limiter = limiter
        self._clock = clock

    # -- helpers --------------------------------------------------------
    @staticmethod
    def _entitled(request: AssistantRequest) -> None:
        """Typed requests. Checked before anything else — before the allowance, before reading
        any file — so a Basic account never costs a model call."""
        if not request.allowed:
            raise UpgradeRequired

    def _period(self) -> str:
        return month_of(self._clock.today())

    def _check_allowance(self, request: AssistantRequest, kind: UsageKind) -> None:
        """Receipt scans and voice notes: the plan must include some, and some must be left.
        Checked first, like ``_entitled``, so a used-up allowance costs nothing."""
        if not request.allowance.includes(kind):
            raise UpgradeRequired
        if self._usage is None or request.allowance.limit(kind) is None:
            return
        used = self._usage.used(request.owner_id, kind, self._period())
        if not request.allowance.has_room(kind, used):
            resets = next_month_start(self._clock.today())
            raise AllowanceUsedUp(kind.value, request.allowance.limit(kind), resets.isoformat())

    def _count(self, request: AssistantRequest, kind: UsageKind) -> None:
        """One more used — only once the model has answered, so a failed request is free.
        Committed straight away: the count stands even if the drafts are never saved."""
        if self._usage is None or self._uow is None:
            return
        self._usage.add_one(request.owner_id, kind, self._period())
        self._uow.commit()

    def usage(self, request: AssistantRequest) -> tuple[UsageSummary, ...]:
        """This month's receipt scans and voice notes: the allowance and how many are used."""
        period = self._period()
        return tuple(
            UsageSummary(
                kind=kind,
                limit=request.allowance.limit(kind),
                used=self._usage.used(request.owner_id, kind, period) if self._usage else 0,
            )
            for kind in UsageKind
        )

    def resets_on(self) -> date:
        return next_month_start(self._clock.today())

    def _today(self, request: AssistantRequest) -> date:
        """The person's today. The server runs on UTC and Ethiopia is UTC+3, so the client's date
        is preferred — but only within a day of the server's, so it cannot be used to backdate."""
        server = self._clock.today()
        client = request.client_today
        if client is not None and abs((client - server).days) <= 1:
            return client
        return server

    def _extract(self, request: AssistantRequest, parts: list[Part], source: str) -> DraftReply:
        # Callers check what the plan allows first: _entitled for text, _check_allowance for
        # voice and receipts.
        self._limiter.take(str(request.owner_id))
        today = self._today(request)
        catalog = self._spending.catalog(request.owner_id, request.calendar, today)
        if not catalog.categories:
            raise ValidationError("Add a category before recording expenses", field="category")
        system = extraction_instructions(
            catalog=catalog,
            today=today,
            language=request.language,
            calendar=request.calendar,
            currency=request.currency,
            source=source,
        )
        answer = self._model.generate_json(
            system=system, parts=parts, schema=extraction_schema(catalog)
        )
        if not isinstance(answer, dict):
            raise AssistantFailed("answer_not_an_object")
        drafts = build_drafts(answer.get("expenses"), catalog, today)
        fixed = build_fixed_drafts(answer.get("fixed_expenses"), catalog, today, request.calendar)
        payments = build_payment_drafts(answer.get("payments"), catalog, today)
        message = answer.get("message") if isinstance(answer.get("message"), str) else ""
        transcript = answer.get("transcript") if isinstance(answer.get("transcript"), str) else ""
        merchant = answer.get("merchant") if isinstance(answer.get("merchant"), str) else ""
        return DraftReply(
            drafts=tuple(drafts),
            message=" ".join(message.split())[:300],
            transcript=(" ".join(transcript.split())[:MAX_TEXT_LENGTH] or None)
            if source == "voice"
            else None,
            merchant=(" ".join(merchant.split())[:80] or None) if source == "receipt" else None,
            fixed_drafts=tuple(fixed),
            payment_drafts=tuple(payments),
        )

    @staticmethod
    def _checked_media(
        data: bytes, content_type: str | None, allowed: dict[str, str], limit: int
    ) -> MediaPart:
        media_type = _media_type(content_type)
        if media_type not in allowed:
            raise UnsupportedMedia(media_type or "unknown", tuple(sorted(set(allowed.values()))))
        if not data:
            raise ValidationError("The file is empty", field="file")
        if len(data) > limit:
            raise MediaTooLarge(len(data), limit)
        return MediaPart(mime_type=allowed[media_type], data=data)

    # -- use cases ------------------------------------------------------
    def read_text(self, request: AssistantRequest, text: str) -> DraftReply:
        self._entitled(request)
        cleaned = " ".join((text or "").split())
        if not cleaned:
            raise ValidationError("Say what you spent", field="text")
        if len(cleaned) > MAX_TEXT_LENGTH:
            raise ValidationError(
                f"Keep it under {MAX_TEXT_LENGTH} characters", field="text"
            )
        return self._extract(request, [TextPart(cleaned)], "text")

    def read_voice(
        self, request: AssistantRequest, data: bytes, content_type: str | None
    ) -> DraftReply:
        self._check_allowance(request, UsageKind.VOICE)
        audio = self._checked_media(data, content_type, AUDIO_TYPES, MAX_AUDIO_BYTES)
        reply = self._extract(request, [audio], "voice")
        self._count(request, UsageKind.VOICE)
        return reply

    def read_receipt(
        self, request: AssistantRequest, data: bytes, content_type: str | None
    ) -> DraftReply:
        self._check_allowance(request, UsageKind.RECEIPT)
        image = self._checked_media(data, content_type, IMAGE_TYPES, MAX_IMAGE_BYTES)
        reply = self._extract(request, [image], "receipt")
        self._count(request, UsageKind.RECEIPT)
        return reply

    def ask_voice(
        self, request: AssistantRequest, data: bytes, content_type: str | None
    ) -> AnswerReply:
        """A question asked out loud: transcribed and answered in one call. Counts as voice."""
        self._check_allowance(request, UsageKind.VOICE)
        audio = self._checked_media(data, content_type, AUDIO_TYPES, MAX_AUDIO_BYTES)
        self._limiter.take(str(request.owner_id))
        today = self._today(request)
        facts = self._spending.month_facts(request.owner_id, request.calendar, today, months=3)
        system = question_instructions(
            facts=facts, today=today, language=request.language, currency=request.currency
        ) + "\nThe question is in the audio: transcribe it into transcript, answer in answer."
        result = self._model.generate_json(system=system, parts=[audio], schema=question_schema())
        answer = result.get("answer") if isinstance(result.get("answer"), str) else ""
        transcript = result.get("transcript") if isinstance(result.get("transcript"), str) else ""
        answer = answer.strip()
        if not answer:
            raise AssistantFailed("empty_answer")
        self._count(request, UsageKind.VOICE)
        return AnswerReply(
            answer=answer[:MAX_ANSWER_LENGTH],
            facts=facts,
            transcript=" ".join(transcript.split())[:MAX_QUESTION_LENGTH] or None,
        )

    def ask(self, request: AssistantRequest, question: str) -> AnswerReply:
        self._entitled(request)
        cleaned = " ".join((question or "").split())
        if not cleaned:
            raise ValidationError("Ask a question", field="question")
        if len(cleaned) > MAX_QUESTION_LENGTH:
            raise ValidationError(
                f"Keep it under {MAX_QUESTION_LENGTH} characters", field="question"
            )
        self._limiter.take(str(request.owner_id))
        today = self._today(request)
        facts = self._spending.month_facts(request.owner_id, request.calendar, today, months=3)
        system = question_instructions(
            facts=facts, today=today, language=request.language, currency=request.currency
        )
        answer = self._model.generate_text(system=system, parts=[TextPart(cleaned)])
        answer = (answer or "").strip()
        if not answer:
            raise AssistantFailed("empty_answer")
        return AnswerReply(answer=answer[:MAX_ANSWER_LENGTH], facts=facts)

