"""The assistant over HTTP, with a scripted language model in place of Gemini.

What they prove:

* With no provider configured the assistant says so (status ``enabled: false``, routes 503).
* Text, voice notes and receipt photos come back as drafts that use the Owner's real category ids,
  in their currency, with dates resolved by the server — and nothing is recorded.
* The model is shown the Owner's categories, units and the person's "today".
* Unsupported or oversized files are refused before the model is called.
* The hourly allowance is enforced, and a model failure is a 502 with the domain error shape.
* Questions are answered from server-computed facts about the Owner's months.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kise.assistant.application.ports import LanguageModel, MediaPart, Part, TextPart
from kise.assistant.domain.errors import AssistantFailed
from kise.platform.api.app import create_app
from kise.platform.clock import FixedClock
from kise.platform.config import Settings
from kise.platform.container import Container

SECRET = "a-secret-long-enough-to-be-taken-seriously-xxxx"


class ScriptedModel(LanguageModel):
    """Answers with whatever the test queued, and remembers what it was asked."""

    def __init__(self) -> None:
        self.json_answers: list[Any] = []
        self.text_answers: list[str] = []
        self.calls: list[dict[str, Any]] = []

    def generate_json(self, *, system: str, parts: list[Part], schema: dict[str, Any]):
        self.calls.append({"system": system, "parts": parts, "schema": schema})
        answer = self.json_answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def generate_text(self, *, system: str, parts: list[Part]) -> str:
        self.calls.append({"system": system, "parts": parts})
        return self.text_answers.pop(0)


def make_client(model: LanguageModel | None, *, hourly_limit: int = 30) -> TestClient:
    settings = Settings(
        _env_file=None,  # a developer's real GEMINI_API_KEY in .env must not leak in
        database_url="sqlite://",
        secret_key=SECRET,
        default_currency="ETB",
        gemini_api_key="",
        assistant_hourly_limit=hourly_limit,
    )
    # 4 September 2026 (ነሐሴ 29, 2018), so the phone's "today" in these tests is believable.
    clock = FixedClock(datetime(2026, 9, 4, 9, 0, tzinfo=UTC))
    container = Container(settings, language_model=model, clock=clock)
    container.create_schema()
    container.seed_units()
    return TestClient(create_app(settings, container=container))


@pytest.fixture
def model() -> ScriptedModel:
    return ScriptedModel()


@pytest.fixture
def client(model: ScriptedModel) -> TestClient:
    return make_client(model)


def signed_in(client: TestClient, email: str = "abebe@example.com", **extra) -> dict[str, str]:
    body = {
        "email": email,
        "password": "correct horse battery",
        "display_name": "Abebe",
        "calendar": "ethiopian",
        "language": "am",
        **extra,
    }
    response = client.post("/api/auth/register", json=body)
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def category_id(client: TestClient, header: dict[str, str], name: str) -> str:
    return next(
        c["id"] for c in client.get("/api/categories", headers=header).json() if c["name"] == name
    )


# -- availability -----------------------------------------------------------


def test_without_a_provider_the_assistant_is_off():
    client = make_client(None)  # settings carry no Gemini key
    header = signed_in(client)
    status = client.get("/api/assistant/status", headers=header).json()
    assert status["enabled"] is False

    response = client.post("/api/assistant/text", json={"text": "taxi 100"}, headers=header)
    assert response.status_code == 503
    assert response.json()["code"] == "assistant_unavailable"


def test_status_when_on(client: TestClient):
    header = signed_in(client)
    body = client.get("/api/assistant/status", headers=header).json()
    assert body["enabled"] is True
    assert body["hourly_limit"] == 30


def test_requires_authentication(client: TestClient):
    assert client.post("/api/assistant/text", json={"text": "x"}).status_code == 401


# -- text -------------------------------------------------------------------


def test_text_becomes_drafts_and_nothing_is_recorded(client: TestClient, model: ScriptedModel):
    header = signed_in(client)
    taxi = category_id(client, header, "Transport")
    food = category_id(client, header, "Food")
    model.json_answers.append(
        {
            "message": "ሁለት ወጪዎች ተገኝተዋል",
            "expenses": [
                {
                    "amount": 150,
                    "category_id": taxi,
                    "date_kind": "today",
                    "payment_method": "telebirr",
                    "note": "ታክሲ",
                },
                {
                    "amount": 40,
                    "category_id": food,
                    "date_kind": "days_ago",
                    "days_ago": 1,
                    "payment_method": "cash",
                    "item_name": "Bread",
                    "quantity": 2,
                    "unit_code": "pcs",
                },
            ],
        }
    )

    response = client.post(
        "/api/assistant/text",
        json={"text": "ዛሬ ለታክሲ 150 ብር በቴሌብር, ትናንት ዳቦ 40", "today": "2026-09-04"},
        headers=header,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["message"] == "ሁለት ወጪዎች ተገኝተዋል"
    assert body["transcript"] is None

    first, second = body["drafts"]
    assert first["category_id"] == taxi
    assert first["category_name"] == "Transport"
    assert first["amount"] == {"amount_minor": 15000, "currency": "ETB"}
    assert first["spent_on"]["gregorian"] == "2026-09-04"
    assert first["spent_on"]["ethiopian"] == "2018-12-29"
    assert first["payment_method"] == "telebirr"
    assert second["spent_on"]["gregorian"] == "2026-09-03"
    units = {u["code"]: u["id"] for u in client.get("/api/units", headers=header).json()}
    if "pcs" in units:
        assert second["item"]["unit_id"] == units["pcs"]

    # The model saw the person's categories by id, their units and their today.
    system = model.calls[0]["system"]
    assert taxi in system and "ትራንስፖርት" in system
    assert "2026-09-04" in system and "2018-12-29" in system
    assert model.calls[0]["parts"] == [TextPart("ዛሬ ለታክሲ 150 ብር በቴሌብር, ትናንት ዳቦ 40")]
    assert taxi in model.calls[0]["schema"]["properties"]["expenses"]["items"]["properties"][
        "category_id"
    ]["enum"]

    # Drafts only: nothing was written.
    assert client.get("/api/expenses", headers=header).json()["total"] == 0


def test_drafts_use_the_owners_currency(client: TestClient, model: ScriptedModel):
    header = signed_in(client, currency="USD")
    model.json_answers.append(
        {
            "message": "ok",
            "expenses": [
                {
                    "amount": 12.5,
                    "category_id": category_id(client, header, "Food"),
                    "date_kind": "today",
                    "payment_method": "card",
                }
            ],
        }
    )
    body = client.post("/api/assistant/text", json={"text": "lunch 12.50"}, headers=header).json()
    assert body["drafts"][0]["amount"] == {"amount_minor": 1250, "currency": "USD"}


def test_a_today_far_from_the_servers_is_ignored(client: TestClient, model: ScriptedModel):
    header = signed_in(client)
    model.json_answers.append({"message": "none", "expenses": []})
    client.post(
        "/api/assistant/text", json={"text": "taxi 100", "today": "2020-01-01"}, headers=header
    )
    assert "2020-01-01" not in model.calls[0]["system"]


def test_nothing_found_is_a_message_not_an_error(client: TestClient, model: ScriptedModel):
    header = signed_in(client)
    model.json_answers.append({"message": "No amount was said", "expenses": []})
    body = client.post("/api/assistant/text", json={"text": "hello"}, headers=header).json()
    assert body == {
        "message": "No amount was said",
        "transcript": None,
        "merchant": None,
        "drafts": [],
    }


# -- voice and receipts -------------------------------------------------------


def test_voice_note_comes_back_with_its_transcript(client: TestClient, model: ScriptedModel):
    header = signed_in(client)
    model.json_answers.append(
        {
            "transcript": "ቡና 30 ብር",
            "message": "አንድ ወጪ",
            "expenses": [
                {
                    "amount": 30,
                    "category_id": category_id(client, header, "Food"),
                    "date_kind": "today",
                    "payment_method": "cash",
                }
            ],
        }
    )
    response = client.post(
        "/api/assistant/voice",
        files={"file": ("note.m4a", b"fake-aac-bytes", "audio/x-m4a")},
        data={"today": "2026-09-04"},
        headers=header,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["transcript"] == "ቡና 30 ብር"
    assert body["drafts"][0]["amount"]["amount_minor"] == 3000

    media = model.calls[0]["parts"][0]
    assert isinstance(media, MediaPart)
    assert media.mime_type == "audio/mp4"  # x-m4a normalised
    assert media.data == b"fake-aac-bytes"


def test_receipt_photo(client: TestClient, model: ScriptedModel):
    header = signed_in(client)
    model.json_answers.append(
        {
            "message": "Receipt from Shoa",
            "merchant": "Shoa Supermarket",
            "expenses": [
                {
                    "amount": 1234.5,
                    "category_id": category_id(client, header, "Food"),
                    "date_kind": "gregorian",
                    "date": "2026-09-02",
                    "payment_method": "card",
                    "note": "Shoa Supermarket",
                }
            ],
        }
    )
    response = client.post(
        "/api/assistant/receipt",
        files={"file": ("r.jpg", b"\xff\xd8\xff", "image/jpeg")},
        headers=header,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["merchant"] == "Shoa Supermarket"
    assert body["drafts"][0]["amount"]["amount_minor"] == 123450
    assert body["drafts"][0]["spent_on"]["gregorian"] == "2026-09-02"
    assert "receipt" in model.calls[0]["system"]


def test_unsupported_file_types_are_refused_before_the_model(
    client: TestClient, model: ScriptedModel
):
    header = signed_in(client)
    response = client.post(
        "/api/assistant/receipt",
        files={"file": ("x.pdf", b"%PDF", "application/pdf")},
        headers=header,
    )
    assert response.status_code == 422
    assert response.json()["code"] == "unsupported_media"
    voice = client.post(
        "/api/assistant/voice",
        files={"file": ("x.jpg", b"\xff\xd8", "image/jpeg")},
        headers=header,
    )
    assert voice.json()["code"] == "unsupported_media"
    assert model.calls == []


def test_oversized_files_are_refused(client: TestClient, model: ScriptedModel):
    header = signed_in(client)
    big = b"\x00" * (8 * 1024 * 1024 + 1)
    response = client.post(
        "/api/assistant/receipt", files={"file": ("r.png", big, "image/png")}, headers=header
    )
    assert response.status_code == 422
    assert response.json()["code"] == "media_too_large"
    assert model.calls == []


def test_an_empty_file_is_refused(client: TestClient):
    header = signed_in(client)
    response = client.post(
        "/api/assistant/voice", files={"file": ("n.m4a", b"", "audio/mp4")}, headers=header
    )
    assert response.status_code == 422


# -- failures and limits --------------------------------------------------------


def test_a_model_failure_is_a_502(client: TestClient, model: ScriptedModel):
    header = signed_in(client)
    model.json_answers.append(AssistantFailed("timeout"))
    response = client.post("/api/assistant/text", json={"text": "taxi 100"}, headers=header)
    assert response.status_code == 502
    assert response.json()["code"] == "assistant_failed"
    assert response.json()["detail"]["reason"] == "timeout"


def test_the_hourly_allowance_is_enforced_per_owner(model: ScriptedModel):
    client = make_client(model, hourly_limit=2)
    abebe = signed_in(client)
    selam = signed_in(client, "selam@example.com")
    model.json_answers.extend([{"message": "", "expenses": []}] * 3)

    for _ in range(2):
        ok = client.post("/api/assistant/text", json={"text": "taxi 100"}, headers=abebe)
        assert ok.status_code == 200
    limited = client.post("/api/assistant/text", json={"text": "taxi 100"}, headers=abebe)
    assert limited.status_code == 429
    assert limited.json()["code"] == "assistant_rate_limited"
    # Someone else's allowance is untouched.
    assert client.post("/api/assistant/text", json={"text": "x"}, headers=selam).status_code == 200


def test_empty_or_long_text_is_a_422(client: TestClient):
    header = signed_in(client)
    assert client.post("/api/assistant/text", json={"text": ""}, headers=header).status_code == 422
    long = client.post("/api/assistant/text", json={"text": "a" * 1001}, headers=header)
    assert long.status_code == 422


# -- questions ------------------------------------------------------------------


def test_questions_are_answered_from_server_facts(client: TestClient, model: ScriptedModel):
    header = signed_in(client)
    client.post(
        "/api/expenses",
        json={
            "category_id": category_id(client, header, "Food"),
            "amount_minor": 125000,
            "spent_on": {"calendar": "ethiopian", "value": "2018-12-20"},
        },
        headers=header,
    )
    model.text_answers.append("በዚህ ወር ለምግብ 1,250 ETB አውጥተዋል።")

    response = client.post(
        "/api/assistant/ask",
        json={"question": "በዚህ ወር ለምግብ ምን ያህል አወጣሁ?", "today": "2026-09-04"},
        headers=header,
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"answer": "በዚህ ወር ለምግብ 1,250 ETB አውጥተዋል።"}

    system = model.calls[0]["system"]
    # Three months of facts, the current one first, with the real figure in it.
    assert '"month": "Nehase 2018"' in system
    assert '"total": "1250.00"' in system
    assert "Hamle 2018" in system and "Sene 2018" in system
    assert "Amharic" in system
