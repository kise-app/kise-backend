"""Fixed Monthly Expenses and the monthly summary, end to end over HTTP.

What they prove:

* A commitment scheduled in ሐምሌ 2018 is owed in ሐምሌ, and asking for July 2026 (Gregorian) finds
  the same Occurrence — the Anchor Calendar at work.
* Settling records what was actually paid; a second settle is a 409; undoing reopens the month.
* Pausing removes it from the month; resuming brings it back; deleting removes it for good.
* The summary adds dynamic spending and commitments, buckets spending per day of the *Ethiopian*
  month, splits by category, and reports last month's total for the change line.
* Another owner can neither see nor settle someone else's commitment.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kise.platform.api.app import create_app
from kise.platform.config import Settings
from kise.platform.container import Container

SECRET = "a-secret-long-enough-to-be-taken-seriously-xxxx"
HAMLE = {"year": 2018, "month": 11, "calendar": "ethiopian"}
HAMLE_QUERY = {"period_year": 2018, "period_month": 11, "period_calendar": "ethiopian"}


@pytest.fixture
def client() -> TestClient:
    settings = Settings(database_url="sqlite://", secret_key=SECRET, default_currency="ETB")
    container = Container(settings)
    container.create_schema()
    container.seed_units()
    return TestClient(create_app(settings, container=container))


def signed_in(client: TestClient, email: str = "abebe@example.com") -> dict[str, str]:
    body = {
        "email": email,
        "password": "correct horse battery",
        "display_name": "Abebe",
        "calendar": "ethiopian",
        "language": "am",
    }
    response = client.post("/api/auth/register", json=body)
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def category_id(client: TestClient, header: dict[str, str], name: str) -> str:
    categories = client.get("/api/categories", headers=header).json()
    return next(c["id"] for c in categories if c["name"] == name)


def schedule_rent(client: TestClient, header: dict[str, str], **overrides) -> dict:
    body = {
        "category_id": category_id(client, header, "Rent"),
        "title": "Rent",
        "amount_minor": 800000,
        "start_period": HAMLE,
        "due_day": 5,
        **overrides,
    }
    response = client.post("/api/fixed-expenses", json=body, headers=header)
    assert response.status_code == 201, response.text
    return response.json()


def record(client: TestClient, header: dict[str, str], category: str, minor: int, day: str):
    response = client.post(
        "/api/expenses",
        json={
            "category_id": category_id(client, header, category),
            "amount_minor": minor,
            "spent_on": {"calendar": "ethiopian", "value": day},
        },
        headers=header,
    )
    assert response.status_code == 201, response.text


def occurrences(client: TestClient, header: dict[str, str], **query) -> dict:
    response = client.get(
        "/api/fixed-expenses/occurrences", params=query or HAMLE_QUERY, headers=header
    )
    assert response.status_code == 200, response.text
    return response.json()


# -- fixed expenses -----------------------------------------------------


def test_requires_authentication(client: TestClient):
    assert client.get("/api/fixed-expenses").status_code == 401


def test_schedule_and_list(client: TestClient):
    header = signed_in(client)
    created = schedule_rent(client, header)

    assert created["title"] == "Rent"
    assert created["amount"] == {"amount_minor": 800000, "currency": "ETB"}
    assert created["anchor_calendar"] == "ethiopian"
    assert created["start_period"]["label_am"] == "ሐምሌ 2018"
    assert created["start_period"]["year"] == 2018
    assert created["due_day"] == 5
    assert created["category_name"] == "Rent"
    assert created["is_paused"] is False

    listed = client.get("/api/fixed-expenses", headers=header).json()
    assert [c["id"] for c in listed] == [created["id"]]


def test_due_day_above_30_is_refused(client: TestClient):
    header = signed_in(client)
    body = {
        "category_id": category_id(client, header, "Rent"),
        "title": "Rent",
        "amount_minor": 800000,
        "start_period": HAMLE,
        "due_day": 31,
    }
    assert client.post("/api/fixed-expenses", json=body, headers=header).status_code == 422


def test_owed_in_its_month_and_found_from_the_gregorian_side(client: TestClient):
    header = signed_in(client)
    schedule_rent(client, header)

    month = occurrences(client, header)
    assert len(month["occurrences"]) == 1
    occurrence = month["occurrences"][0]
    assert occurrence["is_settled"] is False
    assert occurrence["period"]["label_am"] == "ሐምሌ 2018"
    # ሐምሌ 5, 2018 is 12 July 2026.
    assert occurrence["due_date"]["gregorian"] == "2026-07-12"
    assert month["committed"]["amount_minor"] == 800000
    assert month["outstanding"]["amount_minor"] == 800000
    assert month["settled"]["amount_minor"] == 0

    # July 2026 shares most of its days with ሐምሌ, so it is the same Occurrence.
    july = occurrences(
        client, header, period_year=2026, period_month=7, period_calendar="gregorian"
    )
    assert july["occurrences"][0]["period"]["label_am"] == "ሐምሌ 2018"

    # Not owed before it starts.
    sene = occurrences(
        client, header, period_year=2018, period_month=10, period_calendar="ethiopian"
    )
    assert sene["occurrences"] == []


def test_settle_with_actual_amount_then_undo(client: TestClient):
    header = signed_in(client)
    rent = schedule_rent(client, header)
    url = f"/api/fixed-expenses/{rent['id']}/settlements"

    settled = client.post(
        url,
        json={
            "period": HAMLE,
            "amount_minor": 750000,
            "settled_on": {"calendar": "ethiopian", "value": "2018-11-04"},
        },
        headers=header,
    )
    assert settled.status_code == 201, settled.text
    body = settled.json()
    assert body["is_settled"] is True
    assert body["amount"]["amount_minor"] == 750000
    assert body["settled_on"]["ethiopian"] == "2018-11-04"

    month = occurrences(client, header)
    assert month["settled"]["amount_minor"] == 750000
    assert month["outstanding"]["amount_minor"] == 0

    again = client.post(url, json={"period": HAMLE}, headers=header)
    assert again.status_code == 409
    assert again.json()["code"] == "already_settled"

    undone = client.delete(url, params=HAMLE_QUERY, headers=header)
    assert undone.status_code == 200, undone.text
    assert undone.json()["is_settled"] is False
    assert undone.json()["amount"]["amount_minor"] == 800000


def test_settling_a_month_it_is_not_active_in_is_refused(client: TestClient):
    header = signed_in(client)
    rent = schedule_rent(client, header)
    response = client.post(
        f"/api/fixed-expenses/{rent['id']}/settlements",
        json={"period": {"year": 2018, "month": 10, "calendar": "ethiopian"}},
        headers=header,
    )
    assert response.status_code == 409
    assert response.json()["code"] == "period_not_active"


def test_pause_resume_edit_and_delete(client: TestClient):
    header = signed_in(client)
    rent = schedule_rent(client, header)
    base = f"/api/fixed-expenses/{rent['id']}"

    paused = client.post(f"{base}/pause", headers=header)
    assert paused.status_code == 200 and paused.json()["is_paused"] is True
    assert occurrences(client, header)["occurrences"] == []

    resumed = client.post(f"{base}/resume", headers=header)
    assert resumed.json()["is_paused"] is False
    assert len(occurrences(client, header)["occurrences"]) == 1

    edited = client.patch(
        base, json={"title": "House rent", "amount_minor": 900000, "due_day": 30}, headers=header
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["title"] == "House rent"
    assert edited.json()["amount"]["amount_minor"] == 900000
    assert edited.json()["due_day"] == 30

    assert client.delete(base, headers=header).status_code == 204
    assert client.get(base, headers=header).status_code == 404
    assert client.get("/api/fixed-expenses", headers=header).json() == []


def test_another_owner_cannot_reach_a_commitment(client: TestClient):
    abebe = signed_in(client)
    rent = schedule_rent(client, abebe)
    selam = signed_in(client, "selam@example.com")

    assert client.get(f"/api/fixed-expenses/{rent['id']}", headers=selam).status_code == 404
    settle = client.post(
        f"/api/fixed-expenses/{rent['id']}/settlements", json={"period": HAMLE}, headers=selam
    )
    assert settle.status_code == 404
    assert occurrences(client, selam)["occurrences"] == []


# -- monthly summary ----------------------------------------------------


def test_summary_of_an_empty_month(client: TestClient):
    header = signed_in(client)
    response = client.get("/api/reports/summary", params=HAMLE_QUERY, headers=header)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["grand_total"]["amount_minor"] == 0
    assert body["expense_count"] == 0
    assert body["categories"] == []
    assert body["daily_minor"] == [0] * 30
    assert body["period"]["label_am"] == "ሐምሌ 2018"


def test_summary_adds_spending_and_commitments(client: TestClient):
    header = signed_in(client)
    rent = schedule_rent(client, header)
    record(client, header, "Food", 15000, "2018-11-03")
    record(client, header, "Food", 5000, "2018-11-03")
    record(client, header, "Transport", 10000, "2018-11-10")
    record(client, header, "Food", 20000, "2018-10-15")  # ሰኔ: last month
    record(client, header, "Food", 99900, "2018-12-01")  # ነሐሴ: not this month

    body = client.get("/api/reports/summary", params=HAMLE_QUERY, headers=header).json()

    assert body["dynamic_total"]["amount_minor"] == 30000
    assert body["expense_count"] == 3
    assert body["committed"]["amount_minor"] == 800000
    assert body["outstanding"]["amount_minor"] == 800000
    assert body["grand_total"]["amount_minor"] == 830000
    # ሰኔ had 200 ETB of food and no rent yet.
    assert body["previous_total"]["amount_minor"] == 20000

    daily = body["daily_minor"]
    assert len(daily) == 30
    assert daily[2] == 20000  # ሐምሌ 3
    assert daily[9] == 10000  # ሐምሌ 10
    assert sum(daily) == 30000

    names = [c["name"] for c in body["categories"]]
    assert names == ["Rent", "Food", "Transport"]
    rent_row, food_row, _ = body["categories"]
    assert rent_row["committed"]["amount_minor"] == 800000
    assert rent_row["dynamic"]["amount_minor"] == 0
    assert rent_row["icon"] == "home"
    assert food_row["total"]["amount_minor"] == 20000
    assert food_row["expense_count"] == 2

    # Settling for less than committed moves the totals to what was actually paid.
    client.post(
        f"/api/fixed-expenses/{rent['id']}/settlements",
        json={"period": HAMLE, "amount_minor": 700000},
        headers=header,
    )
    settled = client.get("/api/reports/summary", params=HAMLE_QUERY, headers=header).json()
    assert settled["settled"]["amount_minor"] == 700000
    assert settled["outstanding"]["amount_minor"] == 0
    assert settled["grand_total"]["amount_minor"] == 730000


def test_summary_in_a_gregorian_month(client: TestClient):
    header = signed_in(client)
    record(client, header, "Food", 12300, "2018-11-03")  # ሐምሌ 3, 2018 = 10 July 2026
    body = client.get(
        "/api/reports/summary",
        params={"period_year": 2026, "period_month": 7, "period_calendar": "gregorian"},
        headers=header,
    ).json()
    assert len(body["daily_minor"]) == 31
    assert body["daily_minor"][9] == 12300
    assert body["period"]["label_en"] == "July 2026"
