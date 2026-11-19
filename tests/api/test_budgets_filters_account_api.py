"""Budgets, expense filters, the owner's currency and password change, over HTTP.

What they prove:

* Money is written in the signed-in owner's currency, not the server default.
* Budgets are set idempotently (overall and per category), show up in the monthly summary, and a
  category budget appears in the summary even before anything is spent in it.
* Deleting an unused category takes its budget with it.
* The expense list filters by payment method and finds text in the note or the item name, and its
  ``total_amount`` covers the whole filter, not just the page.
* Changing the password needs the current one, and the new one works for sign-in.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kise.platform.api.app import create_app
from kise.platform.config import Settings
from kise.platform.container import Container

SECRET = "a-secret-long-enough-to-be-taken-seriously-xxxx"
HAMLE_QUERY = {"period_year": 2018, "period_month": 11, "period_calendar": "ethiopian"}
PASSWORD = "correct horse battery"


@pytest.fixture
def client() -> TestClient:
    settings = Settings(database_url="sqlite://", secret_key=SECRET, default_currency="ETB")
    container = Container(settings)
    container.create_schema()
    container.seed_units()
    return TestClient(create_app(settings, container=container))


def signed_in(client: TestClient, email: str = "abebe@example.com", **extra) -> dict[str, str]:
    body = {
        "email": email,
        "password": PASSWORD,
        "display_name": "Abebe",
        "calendar": "ethiopian",
        "language": "am",
        **extra,
    }
    response = client.post("/api/auth/register", json=body)
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def category_id(client: TestClient, header: dict[str, str], name: str) -> str:
    categories = client.get("/api/categories", headers=header).json()
    return next(c["id"] for c in categories if c["name"] == name)


def record(client, header, category: str, minor: int, day: str = "2018-11-03", **extra) -> dict:
    response = client.post(
        "/api/expenses",
        json={
            "category_id": category_id(client, header, category),
            "amount_minor": minor,
            "spent_on": {"calendar": "ethiopian", "value": day},
            **extra,
        },
        headers=header,
    )
    assert response.status_code == 201, response.text
    return response.json()


def summary(client: TestClient, header: dict[str, str]) -> dict:
    response = client.get("/api/reports/summary", params=HAMLE_QUERY, headers=header)
    assert response.status_code == 200, response.text
    return response.json()


# -- currency -----------------------------------------------------------


def test_money_is_written_in_the_owners_currency(client: TestClient):
    header = signed_in(client, currency="USD")
    expense = record(client, header, "Food", 1250)
    assert expense["amount"] == {"amount_minor": 1250, "currency": "USD"}
    assert summary(client, header)["currency"] == "USD"


# -- budgets ------------------------------------------------------------


def test_budgets_are_set_idempotently_and_reported_in_the_summary(client: TestClient):
    header = signed_in(client)
    food = category_id(client, header, "Food")
    health = category_id(client, header, "Health")
    record(client, header, "Food", 450000)

    assert client.put("/api/budgets/overall", json={"amount_minor": 1000000}, headers=header)
    again = client.put("/api/budgets/overall", json={"amount_minor": 1500000}, headers=header)
    assert again.status_code == 200
    assert again.json()["category_id"] is None
    assert again.json()["amount"]["amount_minor"] == 1500000

    food_budget = client.put(
        f"/api/budgets/categories/{food}", json={"amount_minor": 500000}, headers=header
    )
    assert food_budget.status_code == 200, food_budget.text
    client.put(f"/api/budgets/categories/{health}", json={"amount_minor": 100000}, headers=header)

    budgets = client.get("/api/budgets", headers=header).json()
    assert len(budgets) == 3

    body = summary(client, header)
    assert body["budget"]["amount_minor"] == 1500000
    by_name = {c["name"]: c for c in body["categories"]}
    assert by_name["Food"]["budget"]["amount_minor"] == 500000
    assert by_name["Food"]["total"]["amount_minor"] == 450000
    # Health has a budget but no spending yet: it still appears, at zero.
    assert by_name["Health"]["total"]["amount_minor"] == 0
    assert by_name["Health"]["budget"]["amount_minor"] == 100000


def test_clearing_budgets(client: TestClient):
    header = signed_in(client)
    food = category_id(client, header, "Food")
    client.put("/api/budgets/overall", json={"amount_minor": 1000000}, headers=header)
    client.put(f"/api/budgets/categories/{food}", json={"amount_minor": 500000}, headers=header)

    assert client.delete("/api/budgets/overall", headers=header).status_code == 204
    assert client.delete(f"/api/budgets/categories/{food}", headers=header).status_code == 204
    assert client.get("/api/budgets", headers=header).json() == []
    assert summary(client, header)["budget"] is None

    missing = client.delete("/api/budgets/overall", headers=header)
    assert missing.status_code == 404
    assert missing.json()["code"] == "budget_not_found"


def test_budget_validation_and_ownership(client: TestClient):
    abebe = signed_in(client)
    food = category_id(client, abebe, "Food")
    zero = client.put("/api/budgets/overall", json={"amount_minor": 0}, headers=abebe)
    assert zero.status_code == 422

    selam = signed_in(client, "selam@example.com")
    stolen = client.put(
        f"/api/budgets/categories/{food}", json={"amount_minor": 1000}, headers=selam
    )
    assert stolen.status_code == 404


def test_deleting_an_unused_category_removes_its_budget(client: TestClient):
    header = signed_in(client)
    created = client.post("/api/categories", json={"name": "Gifts"}, headers=header).json()
    client.put(
        f"/api/budgets/categories/{created['id']}", json={"amount_minor": 1000}, headers=header
    )
    assert client.delete(f"/api/categories/{created['id']}", headers=header).status_code == 204
    assert client.get("/api/budgets", headers=header).json() == []


# -- filters ------------------------------------------------------------


def test_filter_by_payment_method_and_search_note_or_item(client: TestClient):
    header = signed_in(client)
    kg = next(u for u in client.get("/api/units", headers=header).json() if u["code"] == "kg")
    record(client, header, "Food", 15000, note="Lunch with Selam", payment_method="cash")
    record(client, header, "Transport", 10000, note="Taxi to Bole", payment_method="telebirr")
    record(
        client,
        header,
        "Food",
        48000,
        payment_method="cash",
        item={"item_name": "Sugar", "quantity": "12", "unit_id": kg["id"]},
    )

    def listed(**params) -> dict:
        response = client.get("/api/expenses", params={**HAMLE_QUERY, **params}, headers=header)
        assert response.status_code == 200, response.text
        return response.json()

    cash = listed(payment_method="cash")
    assert cash["total"] == 2
    assert cash["total_amount"]["amount_minor"] == 63000

    assert listed(payment_method=["cash", "telebirr"])["total"] == 3
    assert [e["note"] for e in listed(q="selam")["items"]] == ["Lunch with Selam"]
    assert listed(q="SUGAR")["items"][0]["item"]["item_name"] == "Sugar"
    assert listed(q="50%")["total"] == 0  # a literal %, not a wildcard

    # total_amount covers the whole filter, not just the page.
    page = listed(limit=1)
    assert len(page["items"]) == 1
    assert page["total"] == 3
    assert page["total_amount"]["amount_minor"] == 73000


# -- password -----------------------------------------------------------


def test_change_password(client: TestClient):
    header = signed_in(client)
    wrong = client.post(
        "/api/auth/password",
        json={"current_password": "not it at all", "new_password": "a brand new secret"},
        headers=header,
    )
    assert wrong.status_code == 422
    assert wrong.json()["code"] == "current_password_incorrect"

    same = client.post(
        "/api/auth/password",
        json={"current_password": PASSWORD, "new_password": PASSWORD},
        headers=header,
    )
    assert same.status_code == 409

    changed = client.post(
        "/api/auth/password",
        json={"current_password": PASSWORD, "new_password": "a brand new secret"},
        headers=header,
    )
    assert changed.status_code == 204

    old = client.post(
        "/api/auth/login", json={"email": "abebe@example.com", "password": PASSWORD}
    )
    assert old.status_code == 401
    new = client.post(
        "/api/auth/login", json={"email": "abebe@example.com", "password": "a brand new secret"}
    )
    assert new.status_code == 200


def test_change_password_requires_authentication(client: TestClient):
    response = client.post(
        "/api/auth/password", json={"current_password": "x", "new_password": "y" * 10}
    )
    assert response.status_code == 401


def test_an_archived_category_can_be_restored(client: TestClient):
    header = signed_in(client)
    record(client, header, "Clothing", 5000)
    clothing = category_id(client, header, "Clothing")

    archived = client.delete(f"/api/categories/{clothing}", headers=header)
    assert archived.status_code == 200 and archived.json()["is_archived"] is True
    active = [c["name"] for c in client.get("/api/categories", headers=header).json()]
    assert "Clothing" not in active

    restored = client.post(f"/api/categories/{clothing}/restore", headers=header)
    assert restored.status_code == 200, restored.text
    assert restored.json()["is_archived"] is False
    active = [c["name"] for c in client.get("/api/categories", headers=header).json()]
    assert "Clothing" in active
