"""The assistant router.

``GET  /api/assistant/status``    is the assistant switched on here, and the hourly allowance
``POST /api/assistant/text``      {"text": "bread 40, taxi 100"}         -> draft expenses
``POST /api/assistant/voice``     multipart ``file`` (a voice note)       -> transcript + drafts
``POST /api/assistant/receipt``   multipart ``file`` (a receipt photo)    -> drafts
``POST /api/assistant/ask``       {"question": "how much on food?"}      -> an answer
``POST /api/assistant/ask/voice`` multipart ``file`` (a spoken question) -> transcript + answer

Reading also recognises fixed monthly expenses: "rent 8,000 every month on the 5th" comes back as a
``fixed_drafts`` entry (a new commitment), and "I paid the rent" as a ``payment_drafts`` entry for
one of the person's existing commitments. The app schedules or settles those after confirmation.

Every route takes an optional ``today`` (``YYYY-MM-DD``, the phone's date) so "today" means the
person's day rather than the server's UTC one. Nothing here records an expense: the app shows the
drafts, the person confirms, and the app posts them to ``/api/expenses``.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel, Field

from kise.assistant.application.service import (
    MAX_AUDIO_BYTES,
    MAX_IMAGE_BYTES,
    AnswerReply,
    AssistantRequest,
    DraftReply,
)
from kise.assistant.domain.drafts import DraftExpense, FixedDraft, PaymentDraft
from kise.platform.api.dependencies import AssistantDep, AssistantStatusDep
from kise.shared_kernel.domain.calendar.period import Period
from kise.shared_kernel.domain.errors import ValidationError

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


def _parse_today(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValidationError("today must be YYYY-MM-DD", field="today") from exc


def _read_capped(upload: UploadFile, limit: int) -> bytes:
    """Read at most ``limit + 1`` bytes, so an oversized upload is refused without buffering it."""
    return upload.file.read(limit + 1)


# -- schemas -------------------------------------------------------------


class TextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000, examples=["ዛሬ ለታክሲ 150 ብር በቴሌብር"])
    today: str | None = Field(default=None, examples=["2026-09-04"])


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500, examples=["How much on food this month?"])
    today: str | None = None


class DraftItemResponse(BaseModel):
    item_name: str
    quantity: str
    unit_id: str
    unit_code: str


class DraftResponse(BaseModel):
    category_id: str
    category_name: str
    category_name_am: str | None
    amount: dict[str, Any]
    spent_on: dict[str, Any]
    payment_method: str
    note: str | None
    item: DraftItemResponse | None
    warnings: list[str] = Field(
        description="date_unclear, date_in_future, date_long_ago, category_guessed"
    )

    @classmethod
    def of(cls, draft: DraftExpense, currency: str) -> DraftResponse:
        item = draft.item
        return cls(
            category_id=draft.category_id,
            category_name=draft.category_name,
            category_name_am=draft.category_name_am,
            amount={"amount_minor": draft.amount_minor, "currency": currency},
            spent_on=draft.spent_on.describe(),
            payment_method=draft.payment_method,
            note=draft.note,
            item=DraftItemResponse(
                item_name=item.item_name,
                quantity=item.quantity,
                unit_id=item.unit_id,
                unit_code=item.unit_code,
            )
            if item
            else None,
            warnings=list(draft.warnings),
        )


def _period(period: Period) -> dict[str, Any]:
    return {**period.labels(), "year": period.year, "month": period.month}


class FixedDraftResponse(BaseModel):
    title: str
    category_id: str
    category_name: str
    category_name_am: str | None
    amount: dict[str, Any]
    due_day: int
    start_period: dict[str, Any]
    note: str | None
    warnings: list[str]

    @classmethod
    def of(cls, draft: FixedDraft, currency: str) -> FixedDraftResponse:
        return cls(
            title=draft.title,
            category_id=draft.category_id,
            category_name=draft.category_name,
            category_name_am=draft.category_name_am,
            amount={"amount_minor": draft.amount_minor, "currency": currency},
            due_day=draft.due_day,
            start_period=_period(draft.start_period),
            note=draft.note,
            warnings=list(draft.warnings),
        )


class PaymentDraftResponse(BaseModel):
    fixed_expense_id: str
    title: str
    period: dict[str, Any]
    amount: dict[str, Any]
    paid_on: dict[str, Any]
    warnings: list[str] = Field(description="already_paid, date_unclear, ...")

    @classmethod
    def of(cls, draft: PaymentDraft, currency: str) -> PaymentDraftResponse:
        return cls(
            fixed_expense_id=draft.fixed_expense_id,
            title=draft.title,
            period=_period(draft.period),
            amount={"amount_minor": draft.amount_minor, "currency": currency},
            paid_on=draft.paid_on.describe(),
            warnings=list(draft.warnings),
        )


class DraftReplyResponse(BaseModel):
    message: str
    transcript: str | None
    merchant: str | None
    drafts: list[DraftResponse]
    fixed_drafts: list[FixedDraftResponse] = Field(default_factory=list)
    payment_drafts: list[PaymentDraftResponse] = Field(default_factory=list)

    @classmethod
    def of(cls, reply: DraftReply, currency: str) -> DraftReplyResponse:
        return cls(
            message=reply.message,
            transcript=reply.transcript,
            merchant=reply.merchant,
            drafts=[DraftResponse.of(d, currency) for d in reply.drafts],
            fixed_drafts=[FixedDraftResponse.of(d, currency) for d in reply.fixed_drafts],
            payment_drafts=[PaymentDraftResponse.of(d, currency) for d in reply.payment_drafts],
        )


class AnswerResponse(BaseModel):
    answer: str
    transcript: str | None = None

    @classmethod
    def of(cls, reply: AnswerReply) -> AnswerResponse:
        return cls(answer=reply.answer, transcript=reply.transcript)


class StatusResponse(BaseModel):
    enabled: bool
    hourly_limit: int
    max_audio_bytes: int = MAX_AUDIO_BYTES
    max_image_bytes: int = MAX_IMAGE_BYTES


# -- routes --------------------------------------------------------------

Assistant = AssistantDep


@router.get("/status", response_model=StatusResponse)
def status(state: AssistantStatusDep) -> StatusResponse:
    enabled, limit = state
    return StatusResponse(enabled=enabled, hourly_limit=limit)


@router.post("/text", response_model=DraftReplyResponse)
def read_text(body: TextRequest, assistant: Assistant) -> DraftReplyResponse:
    service, request = assistant
    request = _with_today(request, body.today)
    return DraftReplyResponse.of(service.read_text(request, body.text), request.currency)


@router.post("/voice", response_model=DraftReplyResponse)
def read_voice(
    assistant: Assistant,
    file: Annotated[UploadFile, File(description="A voice note: m4a/aac, mp3, wav, ogg, webm")],
    today: Annotated[str | None, Form()] = None,
) -> DraftReplyResponse:
    service, request = assistant
    request = _with_today(request, today)
    data = _read_capped(file, MAX_AUDIO_BYTES)
    reply = service.read_voice(request, data, file.content_type)
    return DraftReplyResponse.of(reply, request.currency)


@router.post("/receipt", response_model=DraftReplyResponse)
def read_receipt(
    assistant: Assistant,
    file: Annotated[UploadFile, File(description="A receipt photo: jpeg, png, webp, heic")],
    today: Annotated[str | None, Form()] = None,
) -> DraftReplyResponse:
    service, request = assistant
    request = _with_today(request, today)
    data = _read_capped(file, MAX_IMAGE_BYTES)
    reply = service.read_receipt(request, data, file.content_type)
    return DraftReplyResponse.of(reply, request.currency)


@router.post("/ask", response_model=AnswerResponse)
def ask(body: QuestionRequest, assistant: Assistant) -> AnswerResponse:
    service, request = assistant
    request = _with_today(request, body.today)
    return AnswerResponse.of(service.ask(request, body.question))


@router.post("/ask/voice", response_model=AnswerResponse)
def ask_voice(
    assistant: Assistant,
    file: Annotated[UploadFile, File(description="A spoken question: m4a/aac, mp3, wav, ...")],
    today: Annotated[str | None, Form()] = None,
) -> AnswerResponse:
    service, request = assistant
    request = _with_today(request, today)
    data = _read_capped(file, MAX_AUDIO_BYTES)
    return AnswerResponse.of(service.ask_voice(request, data, file.content_type))


def _with_today(request: AssistantRequest, today: str | None) -> AssistantRequest:
    return AssistantRequest(
        owner_id=request.owner_id,
        language=request.language,
        calendar=request.calendar,
        currency=request.currency,
        client_today=_parse_today(today),
    )
