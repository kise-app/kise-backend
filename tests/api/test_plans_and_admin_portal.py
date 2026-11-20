"""Plans over HTTP, and the admin portal that manages them.

What they prove:

* New accounts start on a trial of the admin-set length; the profile reports the plan in force.
* The assistant answers 403 ``upgrade_required`` on Basic (or an ended trial) before any model call,
  and works on a trial and on Pro; its status says whether this account may use it.
* The portal only lets in Administrators (app accounts never get in; a removed admin or a changed
  password signs sessions out at once), refuses forms without the CSRF token, throttles sign-in
  attempts, and can change a plan and the trial length.
* The first admin is created at the command line; admins then add, remove and re-password in the
  portal.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from kise.assistant.application.ports import LanguageModel
from kise.identity.application.commands import AppointAdministratorCommand
from kise.platform.admin.cli import main as admin_cli
from kise.platform.admin.router import PORTAL
from kise.platform.api.app import create_app
from kise.platform.clock import FixedClock
from kise.platform.config import Settings
from kise.platform.container import Container

SECRET = "a-secret-long-enough-to-be-taken-seriously-xxxx"
PASSWORD = "correct horse battery"
ADMIN = "admin@kise.example"
ADMIN_PASSWORD = "a long admin password"
NOW = datetime(2026, 9, 4, 9, 0, tzinfo=UTC)


class Clock(FixedClock):
    def advance(self, **delta) -> None:
        self._moment += timedelta(**delta)


class CountingModel(LanguageModel):
    def __init__(self) -> None:
        self.calls = 0

    def generate_json(self, *, system, parts, schema):
        self.calls += 1
        return {"message": "ok", "expenses": []}

    def generate_text(self, *, system, parts):
        self.calls += 1
        return "fine"


@pytest.fixture
def clock() -> Clock:
    return Clock(NOW)


@pytest.fixture
def model() -> CountingModel:
    return CountingModel()


@pytest.fixture
def container(clock: Clock, model: CountingModel) -> Container:
    settings = Settings(
        _env_file=None,
        database_url="sqlite://",
        secret_key=SECRET,
        default_currency="ETB",
    )
    container = Container(settings, language_model=model, clock=clock)
    container.create_schema()
    appoint(container, ADMIN, ADMIN_PASSWORD)
    return container


def appoint(container: Container, email: str, password: str = ADMIN_PASSWORD):
    uow = container.open_unit_of_work()
    try:
        return container.build_services(uow).administrators.appoint(
            AppointAdministratorCommand(email=email, password=password)
        )
    finally:
        uow.session.close()


@pytest.fixture
def client(container: Container) -> TestClient:
    return TestClient(create_app(container.settings, container=container))


def register(client: TestClient, email: str = "abebe@example.com") -> dict:
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": email.split("@")[0]},
    )
    assert response.status_code == 201, response.text
    return response.json()


def bearer(body: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {body['token']}"}


# -- plans in the API -------------------------------------------------------------


def test_a_new_account_starts_on_a_fourteen_day_trial(client: TestClient):
    plan = register(client)["owner"]["plan"]
    assert plan["plan"] == "trial"
    assert plan["effective"] == "trial"
    assert plan["days_left"] == 14
    assert plan["ai_enabled"] is True
    assert plan["expires_at"].startswith("2026-09-18")


def test_after_the_trial_the_account_is_basic_and_the_assistant_is_locked(
    client: TestClient, clock: Clock, model: CountingModel
):
    header = bearer(register(client))
    clock.advance(days=15)

    me = client.get("/api/auth/me", headers=header).json()
    assert me["plan"]["effective"] == "basic"
    assert me["plan"]["ai_enabled"] is False

    status = client.get("/api/assistant/status", headers=header).json()
    assert status["allowed"] is False
    for path, kwargs in (
        ("/api/assistant/text", {"json": {"text": "taxi 100"}}),
        ("/api/assistant/ask", {"json": {"question": "how much?"}}),
        ("/api/assistant/voice", {"files": {"file": ("v.m4a", b"a", "audio/mp4")}}),
        ("/api/assistant/receipt", {"files": {"file": ("r.jpg", b"i", "image/jpeg")}}),
        ("/api/assistant/ask/voice", {"files": {"file": ("q.m4a", b"a", "audio/mp4")}}),
    ):
        response = client.post(path, headers=header, **kwargs)
        assert response.status_code == 403, path
        assert response.json()["code"] == "upgrade_required"
    assert model.calls == 0, "a Basic account never costs a model call"


def test_the_trial_can_use_the_assistant(client: TestClient, model: CountingModel):
    header = bearer(register(client))
    assert client.get("/api/assistant/status", headers=header).json()["allowed"] is True
    assert client.post(
        "/api/assistant/text", json={"text": "taxi 100"}, headers=header
    ).status_code == 200
    assert model.calls == 1


# -- the portal: who gets in ----------------------------------------------------------


def csrf(client: TestClient, path: str = f"{PORTAL}/login") -> str:
    page = client.get(path)
    match = re.search(r'name="csrf" value="([^"]+)"', page.text)
    assert match, page.text[:300]
    return match.group(1)


def admin_login(client: TestClient, email: str = ADMIN, password: str = ADMIN_PASSWORD):
    return client.post(
        f"{PORTAL}/login",
        data={"email": email, "password": password, "csrf": csrf(client)},
        follow_redirects=False,
    )


def signed_in_admin(client: TestClient) -> TestClient:
    response = admin_login(client)
    assert response.status_code == 303, response.text
    return client


def test_pages_need_an_admin_session(client: TestClient):
    for path in (PORTAL, f"{PORTAL}/accounts"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == f"{PORTAL}/login"


def test_only_an_admin_gets_in_and_the_answer_does_not_say_why(client: TestClient):
    register(client, "abebe@example.com")
    register(client, ADMIN)  # an app account with the admin's email is not the admin

    app_user = admin_login(client, "abebe@example.com", PASSWORD)
    app_password = admin_login(client, ADMIN, PASSWORD)
    wrong_password = admin_login(client, ADMIN, "not the password")
    for response in (app_user, app_password, wrong_password):
        assert response.status_code == 401
        assert "do not open the admin portal" in response.text

    ok = admin_login(client, " Admin@Kise.example ")
    assert ok.status_code == 303
    cookie = ok.headers["set-cookie"].lower()
    assert "kise_admin=" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert client.get(PORTAL).status_code == 200


def test_the_portal_is_not_at_slash_admin(client: TestClient):
    assert PORTAL != "/admin"
    for path in ("/admin", "/admin/login"):
        assert client.get(path, follow_redirects=False).status_code == 404


def test_pages_cannot_be_framed_or_cached(client: TestClient):
    signed_in_admin(client)
    headers = client.get(PORTAL).headers
    assert headers["x-frame-options"] == "DENY"
    assert headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]




def test_an_app_bearer_token_is_not_a_portal_session(client: TestClient):
    body = register(client, ADMIN)
    client.cookies.set("kise_admin", body["token"], path=PORTAL)
    assert client.get(PORTAL, follow_redirects=False).status_code == 303


def test_forms_without_the_csrf_token_are_refused(client: TestClient):
    signed_in_admin(client)
    response = client.post(f"{PORTAL}/trial", data={"trial_days": "30", "csrf": "forged"})
    assert response.status_code == 422
    assert "session expired" in response.text
    assert 'value="14"' in client.get(PORTAL).text


def test_sign_in_is_throttled(client: TestClient):
    for _ in range(10):
        admin_login(client, ADMIN, "wrong")
    throttled = admin_login(client)
    assert throttled.status_code == 429
    assert "Too many attempts" in throttled.text


# -- the portal: what it does -------------------------------------------------------


def test_overview_counts_and_trial_length(client: TestClient):
    register(client, "abebe@example.com")
    signed_in_admin(client)
    page = client.get(PORTAL).text
    assert '<p class="mt-1 text-3xl font-semibold" data-testid="total">1</p>' in page

    response = client.post(
        f"{PORTAL}/trial", data={"trial_days": "30", "csrf": csrf(client, PORTAL)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "Trial length saved" in client.get(f"{PORTAL}?saved=trial").text

    later = register(client, "selam@example.com")["owner"]["plan"]
    assert later["days_left"] == 30


def test_a_zero_day_trial_starts_new_accounts_on_basic(client: TestClient):
    signed_in_admin(client)
    client.post(f"{PORTAL}/trial", data={"trial_days": "0", "csrf": csrf(client, PORTAL)})
    assert register(client, "selam@example.com")["owner"]["plan"]["effective"] == "basic"


def test_an_out_of_range_trial_length_is_explained(client: TestClient):
    signed_in_admin(client)
    response = client.post(
        f"{PORTAL}/trial", data={"trial_days": "999", "csrf": csrf(client, PORTAL)}
    )
    assert response.status_code == 422
    assert "from 0 to 365" in response.text


def test_search_and_change_a_plan(client: TestClient, clock: Clock, model: CountingModel):
    abebe = register(client, "abebe@example.com")
    signed_in_admin(client)

    listing = client.get(f"{PORTAL}/accounts", params={"q": "ABEBE"}).text
    assert "abebe@example.com" in listing
    assert ADMIN not in listing.split("<tbody>")[1]

    owner_id = abebe["owner"]["id"]
    detail_path = f"{PORTAL}/accounts/{owner_id}"
    response = client.post(
        f"{detail_path}/plan",
        data={"plan": "pro", "days": "", "csrf": csrf(client, detail_path)},
        follow_redirects=False,
    )
    assert response.status_code == 303

    clock.advance(days=400)  # long after any trial
    me = client.get("/api/auth/me", headers=bearer(abebe)).json()
    assert me["plan"]["effective"] == "pro"
    assert me["plan"]["expires_at"] is None
    assert client.post(
        "/api/assistant/text", json={"text": "taxi 100"}, headers=bearer(abebe)
    ).status_code == 200


def test_bad_plan_changes_are_explained(client: TestClient):
    abebe = register(client, "abebe@example.com")
    signed_in_admin(client)
    path = f"{PORTAL}/accounts/{abebe['owner']['id']}"
    response = client.post(
        f"{path}/plan", data={"plan": "trial", "days": "", "csrf": csrf(client, path)}
    )
    assert response.status_code == 422
    assert "A trial needs a number of days" in response.text


def test_unknown_accounts_are_a_404(client: TestClient):
    signed_in_admin(client)
    assert client.get(f"{PORTAL}/accounts/not-a-uuid").status_code == 404
    missing = "00000000-0000-4000-8000-000000000000"
    assert client.get(f"{PORTAL}/accounts/{missing}").status_code == 404


def test_sign_out(client: TestClient):
    signed_in_admin(client)
    client.post(f"{PORTAL}/logout", data={"csrf": csrf(client, PORTAL)})
    assert client.get(PORTAL, follow_redirects=False).status_code == 303


# -- admins: the first from the command line, the rest in the portal ------------------------


@pytest.fixture
def empty_container(clock: Clock, model: CountingModel) -> Container:
    settings = Settings(
        _env_file=None, database_url="sqlite://", secret_key=SECRET, default_currency="ETB"
    )
    container = Container(settings, language_model=model, clock=clock)
    container.create_schema()
    return container


def run_cli(container: Container, *argv: str, email: str, passwords: list[str]):
    said: list[str] = []
    answers = iter(passwords)
    code = admin_cli(
        list(argv),
        container=container,
        prompt=lambda _: email,
        secret=lambda _: next(answers),
        out=said.append,
    )
    return code, " ".join(said)


def test_with_no_admin_yet_the_login_page_says_how_to_make_one(empty_container: Container):
    client = TestClient(create_app(empty_container.settings, container=empty_container))
    assert "python -m kise.platform.admin create" in client.get(f"{PORTAL}/login").text

    code, said = run_cli(
        empty_container, "create", email="first@kise.example", passwords=[ADMIN_PASSWORD] * 2
    )
    assert code == 0 and "first@kise.example created" in said

    page = client.get(f"{PORTAL}/login").text
    assert "python -m kise.platform.admin" not in page, "once there is an admin, say nothing"
    assert admin_login(client, "first@kise.example").status_code == 303


def test_the_command_line_refuses_bad_input_and_changes_nothing(empty_container: Container):
    mismatch = run_cli(
        empty_container, "create", email="a@kise.example", passwords=[ADMIN_PASSWORD, "other"]
    )
    short = run_cli(empty_container, "create", email="a@kise.example", passwords=["short"] * 2)
    bad_email = run_cli(empty_container, "create", email="nope", passwords=[ADMIN_PASSWORD] * 2)
    assert mismatch == (1, "The two passwords do not match. Nothing was changed.")
    assert short[0] == 1 and "at least 8 characters" in short[1]
    assert bad_email[0] == 1 and "not a valid email" in bad_email[1]

    run_cli(empty_container, "create", email="a@kise.example", passwords=[ADMIN_PASSWORD] * 2)
    again = run_cli(
        empty_container, "create", email="A@kise.example", passwords=[ADMIN_PASSWORD] * 2
    )
    assert again[0] == 1 and "already an admin" in again[1]


def test_the_command_line_resets_a_forgotten_password(client: TestClient, container: Container):
    signed_in_admin(client)
    code, _ = run_cli(
        container, "reset-password", email=ADMIN, passwords=["a fresh admin password"] * 2
    )
    assert code == 0
    assert client.get(PORTAL, follow_redirects=False).status_code == 303, "old session is out"
    assert admin_login(client).status_code == 401
    assert admin_login(client, password="a fresh admin password").status_code == 303

    unknown = run_cli(
        container, "reset-password", email="who@kise.example", passwords=[ADMIN_PASSWORD] * 2
    )
    assert unknown[0] == 1


def test_an_admin_adds_another_who_can_then_sign_in(client: TestClient, container: Container):
    signed_in_admin(client)
    path = f"{PORTAL}/admins"
    response = client.post(
        path,
        data={
            "email": "Selam@Kise.example",
            "password": "selam's first password",
            "confirm": "selam's first password",
            "csrf": csrf(client, path),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get(f"{path}?saved=added").text
    assert page.count('data-testid="admin-row"') == 2
    assert f"by {ADMIN}" in page and "from the command line" in page

    other = TestClient(create_app(container.settings, container=container))
    assert admin_login(other, "selam@kise.example", "selam's first password").status_code == 303


@pytest.mark.parametrize(
    ("form", "message"),
    [
        ({"email": "x@kise.example", "password": "short", "confirm": "short"}, "at least 8"),
        (
            {"email": "x@kise.example", "password": "long enough pass", "confirm": "different"},
            "do not match",
        ),
        (
            {"email": ADMIN, "password": "long enough pass", "confirm": "long enough pass"},
            "already an admin",
        ),
    ],
)
def test_adding_an_admin_explains_what_is_wrong(client: TestClient, form, message):
    signed_in_admin(client)
    path = f"{PORTAL}/admins"
    response = client.post(path, data={**form, "csrf": csrf(client, path)})
    assert response.status_code == 422
    assert message in response.text
    assert response.text.count('data-testid="admin-row"') == 1


def test_removing_an_admin_signs_them_out_and_nobody_removes_themselves(
    client: TestClient, container: Container
):
    selam = appoint(container, "selam@kise.example", "selam's long password")
    other = TestClient(create_app(container.settings, container=container))
    assert admin_login(other, "selam@kise.example", "selam's long password").status_code == 303

    signed_in_admin(client)
    me = client.get(f"{PORTAL}/admins").text
    assert me.count("Remove selam@kise.example") == 1
    assert f"Remove {ADMIN}" not in me, "no button to remove yourself"

    removed = client.post(
        f"{PORTAL}/admins/{selam.administrator_id}/remove",
        data={"csrf": csrf(client, f"{PORTAL}/admins")},
        follow_redirects=False,
    )
    assert removed.status_code == 303
    assert other.get(PORTAL, follow_redirects=False).status_code == 303, "selam is signed out"

    # No button for it, and posting directly is refused too.
    third = appoint(container, "third@kise.example", "third's long password")
    third_client = TestClient(create_app(container.settings, container=container))
    admin_login(third_client, "third@kise.example", "third's long password")
    response = third_client.post(
        f"{PORTAL}/admins/{third.administrator_id}/remove",
        data={"csrf": csrf(third_client, f"{PORTAL}/admins")},
    )
    assert response.status_code == 422
    assert "cannot remove yourself" in response.text


def test_an_admin_changes_their_password_and_other_sessions_end(
    client: TestClient, container: Container
):
    signed_in_admin(client)
    laptop = TestClient(create_app(container.settings, container=container))
    signed_in_admin(laptop)

    path = f"{PORTAL}/password"
    wrong = client.post(
        path,
        data={
            "current_password": "not it",
            "new_password": "a new admin password",
            "confirm": "a new admin password",
            "csrf": csrf(client, path),
        },
    )
    assert wrong.status_code == 422 and "current password is incorrect" in wrong.text

    changed = client.post(
        path,
        data={
            "current_password": ADMIN_PASSWORD,
            "new_password": "a new admin password",
            "confirm": "a new admin password",
            "csrf": csrf(client, path),
        },
        follow_redirects=False,
    )
    assert changed.status_code == 303
    assert client.get(f"{path}?saved=password").status_code == 200, "this session carries on"
    assert laptop.get(PORTAL, follow_redirects=False).status_code == 303, "the other one ends"
    assert admin_login(laptop, password="a new admin password").status_code == 303


# -- paying for Pro by hand -----------------------------------------------------------


def payment_form(client: TestClient, **fields) -> dict:
    form = {
        "bank": ["Commercial Bank of Ethiopia", "telebirr", "", "", ""],
        "holder": ["Kise PLC", "Kise PLC", "", "", ""],
        "number": ["1000123456789", "0911234567", "", "", ""],
        "price": "300 birr a month",
        "telegram": "https://t.me/kise_support",
        "phone": "+251 911 234 567",
        "email": "pay@kise.example",
        "note": "We switch you to Pro within a day.",
        "csrf": csrf(client, f"{PORTAL}/payments"),
    }
    form.update(fields)
    return form


def test_payment_details_start_empty_and_need_sign_in(client: TestClient):
    body = register(client)
    assert client.get("/api/plans/payment").status_code == 401
    info = client.get("/api/plans/payment", headers=bearer(body)).json()
    assert info["ready"] is False
    assert info["accounts"] == []
    assert info["reference"] == "abebe@example.com"


def test_an_admin_sets_payment_details_and_the_app_shows_them(client: TestClient):
    body = register(client)
    signed_in_admin(client)
    assert "Not shown in the app yet" in client.get(f"{PORTAL}/payments").text

    saved = client.post(f"{PORTAL}/payments", data=payment_form(client), follow_redirects=False)
    assert saved.status_code == 303
    page = client.get(f"{PORTAL}/payments?saved=payments").text
    assert "The app shows these details now" in page
    assert 'value="@kise_support"' in page
    assert page.count('data-testid="payment-row"') == 5

    info = client.get("/api/plans/payment", headers=bearer(body)).json()
    assert info == {
        "ready": True,
        "accounts": [
            {
                "bank": "Commercial Bank of Ethiopia",
                "holder": "Kise PLC",
                "number": "1000123456789",
            },
            {"bank": "telebirr", "holder": "Kise PLC", "number": "0911234567"},
        ],
        "price": "300 birr a month",
        "telegram": "kise_support",
        "telegram_url": "https://t.me/kise_support",
        "phone": "+251 911 234 567",
        "email": "pay@kise.example",
        "note": "We switch you to Pro within a day.",
        "reference": "abebe@example.com",
    }


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"number": ["", "0911234567", "", "", ""]}, "Account number is required"),
        ({"telegram": "@x"}, "Telegram username"),
        ({"phone": "call us"}, "phone number"),
    ],
)
def test_bad_payment_details_are_explained_and_kept(client: TestClient, fields, message):
    signed_in_admin(client)
    response = client.post(f"{PORTAL}/payments", data=payment_form(client, **fields))
    assert response.status_code == 422
    assert message in response.text
    assert 'value="300 birr a month"' in response.text, "what was typed is kept"
    assert "Not shown in the app yet" not in response.text
