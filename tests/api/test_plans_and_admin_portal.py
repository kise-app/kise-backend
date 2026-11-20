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
from kise.assistant.domain.errors import AssistantFailed
from kise.identity.application.commands import AppointAdministratorCommand
from kise.identity.domain.models import PlanCode, Subscription
from kise.platform.admin.cli import main as admin_cli
from kise.platform.admin.router import PORTAL
from kise.platform.api.app import create_app
from kise.platform.clock import FixedClock
from kise.platform.config import Settings
from kise.platform.container import Container
from kise.shared_kernel.domain.identifiers import OwnerId

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
        self.fail = False

    def generate_json(self, *, system, parts, schema):
        self.calls += 1
        if self.fail:
            raise AssistantFailed("model_down")
        return {"message": "ok", "expenses": [], "answer": "fine", "transcript": "how much?"}

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
    assert (
        client.post("/api/assistant/text", json={"text": "taxi 100"}, headers=header).status_code
        == 200
    )
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
    response = client.post(f"{PORTAL}/plans/trial", data={"trial_days": "30", "csrf": "forged"})
    assert response.status_code == 422
    assert "session expired" in response.text
    assert "14 days" in client.get(f"{PORTAL}/plans").text


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

    assert "14 days of Pro features" in page

    plans = f"{PORTAL}/plans"
    response = save_card(client, "trial", trial_days="30")
    assert response.status_code == 303
    assert response.headers["location"] == f"{plans}?saved=trial"
    assert "Saved." in client.get(f"{plans}?saved=trial").text
    assert "30 days of Pro features" in client.get(PORTAL).text

    later = register(client, "selam@example.com")["owner"]["plan"]
    assert later["days_left"] == 30


def test_a_zero_day_trial_starts_new_accounts_on_basic(client: TestClient):
    signed_in_admin(client)
    save_card(client, "trial", trial_days="0")
    assert register(client, "selam@example.com")["owner"]["plan"]["effective"] == "basic"


def test_an_out_of_range_trial_length_is_explained(client: TestClient):
    signed_in_admin(client)
    response = save_card(client, "trial", trial_days="999")
    assert response.status_code == 422
    assert "from 0 to 365" in response.text
    assert 'value="999"' in response.text, "what was typed stays in the open card"


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
    assert (
        client.post(
            "/api/assistant/text", json={"text": "taxi 100"}, headers=bearer(abebe)
        ).status_code
        == 200
    )


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
        "telegram": "https://t.me/kise_support",
        "phone": "+251 911 234 567",
        "email": "pay@kise.example",
        "note": "We switch you to Pro within a day.",
        "csrf": csrf(client, f"{PORTAL}/payments"),
    }
    form.update(fields)
    return form


#: What each built-in card's form holds before anything is changed.
CARD_DEFAULTS = {
    "trial": {
        "name": "Trial",
        "trial_days": "14",
        "typed_requests": "1",
        "receipts": "",
        "voice": "",
    },
    "basic": {
        "name": "Basic",
        "price": "",
        "typed_requests": "",
        "receipts": "0",
        "voice": "0",
        "visible": "1",
    },
    "pro": {
        "name": "Pro",
        "price": "",
        "typed_requests": "1",
        "receipts": "",
        "voice": "",
        "visible": "1",
    },
}


def save_card(client: TestClient, code: str, **fields):
    """Save a plan card as the form would: its current values, with ``fields`` changed. A form
    leaves an unticked box out, so a field set to "" is dropped."""
    plans = f"{PORTAL}/plans"
    data = {"voice_seconds": "60"} | CARD_DEFAULTS.get(code, {}) | fields
    data = {
        key: value
        for key, value in data.items()
        if value != "" or key in {"price", "receipts", "voice", "trial_days", "name"}
    }
    data["csrf"] = csrf(client, f"{plans}?edit={code}")
    return client.post(f"{plans}/{code}", data=data, follow_redirects=False)


def set_price(client: TestClient, plan: str, price: str):
    return save_card(client, plan, price=price)


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
    set_price(client, "basic", "100 birr a month")
    set_price(client, "pro", "300 birr a month")

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
        "plans": [
            {
                "plan": "basic",
                "name": "Basic",
                "price": "100 birr a month",
                "ai_enabled": False,
                "typed_requests": False,
                "receipts_per_month": 0,
                "voice_per_month": 0,
                "current": False,
            },
            {
                "plan": "pro",
                "name": "Pro",
                "price": "300 birr a month",
                "ai_enabled": True,
                "typed_requests": True,
                "receipts_per_month": None,
                "voice_per_month": None,
                "current": False,
            },
        ],
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
    assert "We switch you to Pro within a day." in response.text, "what was typed is kept"
    assert "Not shown in the app yet" not in response.text


# -- layout ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "current"),
    [
        (PORTAL, "Overview"),
        (f"{PORTAL}/accounts", "Accounts"),
        (f"{PORTAL}/plans", "Plans"),
        (f"{PORTAL}/payments", "Payments"),
        (f"{PORTAL}/admins", "Admins"),
    ],
)
def test_every_page_has_the_sidebar_with_its_own_link_marked(
    client: TestClient, path: str, current: str
):
    signed_in_admin(client)
    page = client.get(path).text
    sidebar = page.split('data-testid="sidebar"')[1].split("</aside>")[0]
    for label in ("Overview", "Accounts", "Plans", "Payments", "Admins", "Your password"):
        assert label in sidebar
    assert "Sign out" in sidebar
    assert 'data-testid="menu-button"' in page and 'aria-controls="admin-sidebar"' in page
    assert '<script src="/static/admin.js' in page
    marked = re.findall(
        r'aria-current="page">\s*<svg.*?</svg><span[^>]*>([^<]+)</span>', sidebar, re.S
    )
    assert marked == [current]
    assert ADMIN in sidebar


def test_the_sign_in_page_has_no_sidebar(client: TestClient):
    page = client.get(f"{PORTAL}/login").text
    assert 'data-testid="sidebar"' not in page and "admin.js" not in page


def test_the_menu_script_is_served_and_allowed(client: TestClient):
    signed_in_admin(client)
    assert "script-src 'self'" in client.get(PORTAL).headers["content-security-policy"]
    script = client.get("/static/admin.js")
    assert script.status_code == 200 and "javascript" in script.headers["content-type"]


# -- plan cards ---------------------------------------------------------------------


def test_plan_cards_show_each_plan_and_open_one_for_editing(client: TestClient):
    register(client)
    signed_in_admin(client)
    page = client.get(f"{PORTAL}/plans").text
    for plan in ("trial", "basic", "pro"):
        assert f'data-testid="plan-card-{plan}"' in page
        assert f'data-testid="edit-{plan}"' in page
    assert "No price set" in page
    trial_card = page.split('data-testid="plan-card-trial"')[1].split("</section>")[0]
    assert "<form" not in trial_card, "read-only until Edit"

    editing = client.get(f"{PORTAL}/plans?edit=pro").text
    pro = editing.split('data-testid="plan-card-pro"')[1]
    assert 'name="price"' in pro and 'data-testid="edit-pro"' not in pro
    assert 'name="price"' not in editing.split('data-testid="plan-card-pro"')[0]


def test_saving_a_price_changes_only_that_plan(client: TestClient):
    signed_in_admin(client)
    saved = set_price(client, "pro", "300 birr a month")
    assert saved.status_code == 303
    assert saved.headers["location"] == f"{PORTAL}/plans?saved=pro"
    set_price(client, "basic", "100 birr a month")
    set_price(client, "pro", "350 birr a month")

    page = client.get(f"{PORTAL}/plans?saved=pro").text
    assert "Saved." in page
    assert "350 birr a month" in page and "100 birr a month" in page


def test_editing_a_plan_keeps_payment_details_and_the_other_way_round(client: TestClient):
    signed_in_admin(client)
    set_price(client, "pro", "300 birr a month")
    client.post(f"{PORTAL}/payments", data=payment_form(client))
    assert "300 birr a month" in client.get(f"{PORTAL}/plans").text
    set_price(client, "basic", "100 birr a month")
    assert 'value="@kise_support"' in client.get(f"{PORTAL}/payments").text


def test_a_bad_plan_edit_stays_open_with_the_error(client: TestClient):
    signed_in_admin(client)
    response = set_price(client, "pro", "x" * 121)
    assert response.status_code == 422
    assert "Keep the price under 120 characters" in response.text
    assert 'name="price"' in response.text.split('data-testid="plan-card-pro"')[1]
    assert (
        client.post(
            f"{PORTAL}/plans/gold", data={"csrf": csrf(client, f"{PORTAL}/plans")}
        ).status_code
        == 404
    )


# -- monthly allowances of receipt scans and voice notes ------------------------------

RECEIPT = ("/api/assistant/receipt", {"files": {"file": ("r.jpg", b"i", "image/jpeg")}})
VOICE = ("/api/assistant/voice", {"files": {"file": ("v.m4a", b"a", "audio/mp4")}})
ASK_VOICE = ("/api/assistant/ask/voice", {"files": {"file": ("q.m4a", b"a", "audio/mp4")}})
TEXT = ("/api/assistant/text", {"json": {"text": "taxi 100"}})


def send(client: TestClient, header: dict, route) -> int:
    path, kwargs = route
    return client.post(path, headers=header, **kwargs).status_code


def set_allowance(client: TestClient, plan: str, receipts: str, voice: str, **extra):
    return save_card(client, plan, receipts=receipts, voice=voice, **extra)


def usage(client: TestClient, header: dict) -> dict:
    status = client.get("/api/assistant/status", headers=header).json()
    return {item["kind"]: item for item in status["usage"]} | {"resets_on": status["resets_on"]}


def test_by_default_trial_and_pro_have_no_monthly_limit(client: TestClient):
    header = bearer(register(client))
    for _ in range(3):
        assert send(client, header, RECEIPT) == 200
    now = usage(client, header)
    assert now["receipt"] == {"kind": "receipt", "limit": None, "used": 3, "remaining": None}
    assert now["voice"]["limit"] is None
    assert now["resets_on"] == "2026-10-01"


def test_a_used_up_allowance_is_refused_before_any_model_call(
    client: TestClient, model: CountingModel
):
    signed_in_admin(client)
    assert set_allowance(client, "trial", receipts="2", voice="1").status_code == 303
    header = bearer(register(client))

    assert send(client, header, RECEIPT) == 200
    assert send(client, header, RECEIPT) == 200
    calls = model.calls
    third = client.post(RECEIPT[0], headers=header, **RECEIPT[1])
    assert third.status_code == 403
    assert third.json()["code"] == "allowance_used_up"
    assert third.json()["detail"] == {"kind": "receipt", "limit": 2, "resets_on": "2026-10-01"}
    assert model.calls == calls, "no model call once the allowance is used up"

    # Voice is counted separately, and asking aloud is voice too.
    assert send(client, header, ASK_VOICE) == 200
    assert send(client, header, VOICE) == 403
    # Typed requests have no monthly allowance.
    assert send(client, header, TEXT) == 200

    now = usage(client, header)
    assert now["receipt"]["used"] == 2 and now["receipt"]["remaining"] == 0
    assert now["voice"]["used"] == 1 and now["voice"]["remaining"] == 0


def test_the_counts_start_again_next_month(client: TestClient, clock: Clock):
    signed_in_admin(client)
    # A 60-day trial, so the account is still on it in October.
    set_allowance(client, "trial", receipts="1", voice="", trial_days="60")
    header = bearer(register(client))
    assert send(client, header, RECEIPT) == 200
    assert send(client, header, RECEIPT) == 403
    clock.advance(days=27)  # 1 October
    assert send(client, header, RECEIPT) == 200


def test_a_failed_request_is_not_counted(client: TestClient, model: CountingModel):
    signed_in_admin(client)
    set_allowance(client, "trial", receipts="1", voice="")
    header = bearer(register(client))
    model.fail = True
    assert send(client, header, RECEIPT) == 502
    model.fail = False
    assert send(client, header, RECEIPT) == 200
    assert usage(client, header)["receipt"]["used"] == 1


def test_basic_can_be_given_scans_and_voice_but_not_typed_requests(
    client: TestClient, clock: Clock
):
    signed_in_admin(client)
    set_allowance(client, "basic", receipts="3", voice="0")
    header = bearer(register(client))
    clock.advance(days=15)  # the trial is over: Basic now

    assert send(client, header, RECEIPT) == 200
    assert send(client, header, VOICE) == 403
    response = client.post(TEXT[0], headers=header, **TEXT[1])
    assert response.json()["code"] == "upgrade_required"
    assert usage(client, header)["receipt"]["limit"] == 3


def test_plan_cards_show_and_edit_the_allowance(client: TestClient):
    signed_in_admin(client)
    page = client.get(f"{PORTAL}/plans").text
    basic = page.split('data-testid="allowance-basic"')[1].split("</dl>")[0]
    assert "No scans" in basic and "No voice notes" in basic
    assert "No limit on scans" in page.split('data-testid="allowance-pro"')[1].split("</dl>")[0]

    saved = set_allowance(client, "pro", receipts="1,000", voice="200", price="300 birr a month")
    assert saved.status_code == 303
    pro = client.get(f"{PORTAL}/plans").text.split('data-testid="plan-card-pro"')[1]
    assert "1,000 scans a month" in pro and "200 voice notes a month" in pro
    assert "300 birr a month" in pro


@pytest.mark.parametrize(
    ("receipts", "voice", "message"),
    [
        ("-1", "", "from 0 to 10,000"),
        ("lots", "", "Enter receipt scans as a whole number"),
        ("", "20000", "from 0 to 10,000"),
    ],
)
def test_a_bad_allowance_is_explained_and_changes_nothing(
    client: TestClient, receipts: str, voice: str, message: str
):
    signed_in_admin(client)
    set_price(client, "pro", "300 birr a month")
    response = set_allowance(client, "pro", receipts=receipts, voice=voice, price="999 birr")
    assert response.status_code == 422
    assert message in response.text
    assert f'value="{receipts}"' in response.text.split('data-testid="plan-card-pro"')[1]
    after = client.get(f"{PORTAL}/plans").text
    assert "300 birr a month" in after and "999 birr" not in after, "nothing was saved"


# -- how long a voice note may be ---------------------------------------------------------


def m4a(seconds: float) -> bytes:
    """A minimal MP4 with just enough boxes for its length to be read (moov > mvhd)."""
    import struct

    timescale = 1000
    mvhd = struct.pack(">B3xII", 0, 0, 0) + struct.pack(">II", timescale, int(seconds * timescale))
    mvhd_box = struct.pack(">I4s", 8 + len(mvhd), b"mvhd") + mvhd
    moov = struct.pack(">I4s", 8 + len(mvhd_box), b"moov") + mvhd_box
    ftyp = struct.pack(">I4s", 16, b"ftyp") + b"M4A \x00\x00\x00\x00"
    return ftyp + moov + b"\x00" * 64


def voice_note(
    client: TestClient, header: dict, seconds: float, path: str = "/api/assistant/voice"
):
    return client.post(path, headers=header, files={"file": ("v.m4a", m4a(seconds), "audio/mp4")})


def test_a_voice_note_longer_than_the_plan_allows_is_refused_before_the_model(
    client: TestClient, model: CountingModel
):
    signed_in_admin(client)
    save_card(client, "trial", voice_seconds="20")
    header = bearer(register(client))
    assert client.get("/api/assistant/status", headers=header).json()["max_voice_seconds"] == 20

    assert voice_note(client, header, 21).status_code == 200, "a second or two over is fine"
    calls = model.calls
    for path in ("/api/assistant/voice", "/api/assistant/ask/voice"):
        response = voice_note(client, header, 45, path)
        assert response.status_code == 422
        assert response.json()["code"] == "voice_note_too_long"
        assert response.json()["detail"]["max_seconds"] == 20
    assert model.calls == calls
    assert usage(client, header)["voice"]["used"] == 1, "a refused note is not counted"


def test_a_recording_of_unknown_length_is_held_to_a_size_limit(client: TestClient):
    header = bearer(register(client))  # 60 seconds by default
    ogg = lambda size: {"file": ("v.ogg", b"O" * size, "audio/ogg")}  # noqa: E731
    small = client.post("/api/assistant/voice", headers=header, files=ogg(200_000))
    assert small.status_code == 200
    too_big = client.post("/api/assistant/voice", headers=header, files=ogg(3_000_000))
    assert too_big.json()["code"] == "voice_note_too_long"


def test_the_plan_card_shows_and_checks_the_voice_length(client: TestClient):
    signed_in_admin(client)
    page = client.get(f"{PORTAL}/plans").text
    assert "60 seconds" in page.split('data-testid="voice-seconds-pro"')[1][:30]
    response = save_card(client, "pro", voice_seconds="600")
    assert response.status_code == 422
    assert "from 5 to 300 seconds" in response.text


# -- admins add, hide and remove plans --------------------------------------------------


def add_plan(client: TestClient, **fields):
    plans = f"{PORTAL}/plans"
    data = {
        "name": "Family",
        "price": "500 birr a month",
        "typed_requests": "1",
        "receipts": "100",
        "voice": "40",
        "voice_seconds": "90",
        "visible": "1",
    } | fields
    data = {key: value for key, value in data.items() if value is not None}
    data["csrf"] = csrf(client, f"{plans}?edit=new")
    return client.post(plans, data=data, follow_redirects=False)


def put_on_plan(client: TestClient, owner_id: str, plan: str, days: str = ""):
    path = f"{PORTAL}/accounts/{owner_id}"
    return client.post(
        f"{path}/plan",
        data={"plan": plan, "days": days, "csrf": csrf(client, path)},
        follow_redirects=False,
    )


def test_an_added_plan_gets_a_card_is_offered_in_the_app_and_can_be_given(
    client: TestClient,
):
    abebe = register(client)
    signed_in_admin(client)
    assert 'data-testid="plan-card-new"' in client.get(f"{PORTAL}/plans?edit=new").text

    added = add_plan(client)
    assert added.status_code == 303
    assert added.headers["location"] == f"{PORTAL}/plans?saved=family"
    card = client.get(f"{PORTAL}/plans").text.split('data-testid="plan-card-family"')[1]
    assert "500 birr a month" in card and "100 scans a month" in card
    assert 'data-testid="remove-family"' in card

    offered = client.get("/api/plans/payment", headers=bearer(abebe)).json()["plans"]
    assert [p["plan"] for p in offered] == ["basic", "pro", "family"]
    family = offered[2]
    assert family["name"] == "Family" and family["receipts_per_month"] == 100

    account = client.get(f"{PORTAL}/accounts/{abebe['owner']['id']}").text
    assert 'value="family"' in account
    assert put_on_plan(client, abebe["owner"]["id"], "family", "30").status_code == 303
    me = client.get("/api/auth/me", headers=bearer(abebe)).json()["plan"]
    assert me["effective"] == "family" and me["name"] == "Family"
    assert me["receipts_per_month"] == 100 and me["max_voice_seconds"] == 90
    status = client.get("/api/assistant/status", headers=bearer(abebe)).json()
    assert status["allowed"] is True and status["max_voice_seconds"] == 90


def test_a_plan_without_typed_requests_still_gets_its_scans(client: TestClient):
    abebe = register(client)
    signed_in_admin(client)
    add_plan(client, name="Scanner", typed_requests=None, receipts="5", voice="0")
    put_on_plan(client, abebe["owner"]["id"], "scanner")
    header = bearer(abebe)
    assert send(client, header, RECEIPT) == 200
    assert send(client, header, TEXT) == 403
    assert send(client, header, VOICE) == 403


def test_a_plan_name_is_needed_and_must_be_new(client: TestClient):
    signed_in_admin(client)
    blank = add_plan(client, name="  ")
    assert blank.status_code == 422 and "A plan needs a name" in blank.text
    assert 'data-testid="plan-card-new"' in blank.text, "the form stays open"
    clash = add_plan(client, name="PRO")
    assert clash.status_code == 422 and "already a plan with that name" in clash.text
    assert 'value="PRO"' in clash.text


def test_a_hidden_plan_is_not_offered_but_still_works_for_its_accounts(client: TestClient):
    abebe = register(client)
    signed_in_admin(client)
    add_plan(client)
    put_on_plan(client, abebe["owner"]["id"], "family")
    saved = save_card(
        client,
        "family",
        name="Family",
        price="500 birr a month",
        typed_requests="1",
        receipts="100",
        voice="40",
        visible="",
    )
    assert saved.status_code == 303
    page = client.get(f"{PORTAL}/plans").text
    assert 'data-testid="hidden-family"' in page
    offered = client.get("/api/plans/payment", headers=bearer(abebe)).json()["plans"]
    assert "family" not in [p["plan"] for p in offered]
    assert client.get("/api/auth/me", headers=bearer(abebe)).json()["plan"]["name"] == "Family"


def test_a_plan_is_removed_only_when_nobody_is_on_it_and_never_if_built_in(
    client: TestClient,
):
    abebe = register(client)
    signed_in_admin(client)
    add_plan(client)
    put_on_plan(client, abebe["owner"]["id"], "family")

    def remove(code: str):
        return client.post(
            f"{PORTAL}/plans/{code}/remove",
            data={"csrf": csrf(client, f"{PORTAL}/plans")},
            follow_redirects=False,
        )

    in_use = remove("family")
    assert in_use.status_code == 422 and "1 account is on Family" in in_use.text
    assert "In use: hide it instead" in client.get(f"{PORTAL}/plans").text
    for built_in in ("trial", "basic"):
        response = remove(built_in)
        assert response.status_code == 422 and "is built in" in response.text

    put_on_plan(client, abebe["owner"]["id"], "basic")
    removed = remove("family")
    assert removed.status_code == 303
    assert "Family removed." in client.get(removed.headers["location"]).text
    assert 'data-testid="plan-card-family"' not in client.get(f"{PORTAL}/plans").text
    assert remove("family").status_code == 404


def test_an_account_on_a_plan_that_no_longer_exists_reads_as_basic(client, container):
    abebe = register(client)
    uow = container.open_unit_of_work()
    owner = uow.owners.get(OwnerId.parse(abebe["owner"]["id"]))
    owner.change_subscription(Subscription(PlanCode("gone")))
    uow.commit()
    uow.session.close()
    me = client.get("/api/auth/me", headers=bearer(abebe)).json()["plan"]
    assert me["name"] == "Basic" and me["ai_enabled"] is False
