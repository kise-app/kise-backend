"""The admin portal's pages.

``GET  /kise-office``                         plan overview and the trial length for new accounts
``POST /kise-office/trial``                   change the trial length
``GET  /kise-office/accounts``                accounts, searchable, 50 a page
``GET  /kise-office/accounts/{id}``           one account, with its plan
``POST /kise-office/accounts/{id}/plan``      Basic, a trial of N days, or Pro (N days or open)
``GET  /kise-office/payments`` · ``POST``     how people pay for Pro (shown in the app)
``GET  /kise-office/admins``                  who can sign in here; add one
``POST /kise-office/admins``                  add an admin (email and a first password)
``POST /kise-office/admins/{id}/remove``      remove another admin
``GET  /kise-office/password`` · ``POST``     change your own password
``GET  /kise-office/login`` · ``POST``        sign in as an admin
``POST /kise-office/logout``

Admins are ``Administrator`` aggregates, not app accounts. The first is created at the command line
(``python -m kise.platform.admin create``), which is also how a forgotten password is reset.

Server-rendered (Jinja + the Tailwind bundle the landing page uses): no JavaScript, so there is no
client to keep in step with the API. Every page is ``no-store`` and cannot be framed; every form is
CSRF-checked; sign-in attempts are throttled per address.
"""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape

from kise.identity.application.commands import (
    AppointAdministratorCommand,
    AuthenticateAdministratorCommand,
    ChangeAdministratorPasswordCommand,
    ChangePlanCommand,
    ListAccountsQuery,
    RemoveAdministratorCommand,
    SetPaymentInstructionsCommand,
    SetTrialLengthCommand,
)
from kise.identity.application.views import AdministratorView
from kise.identity.domain.errors import InvalidCredentials
from kise.identity.domain.models import (
    MAX_GRANT_DAYS,
    MAX_PAYMENT_ACCOUNTS,
    MAX_TRIAL_DAYS,
    MIN_ADMIN_PASSWORD_LENGTH,
    PaymentInstructions,
    Plan,
)
from kise.platform.admin.sessions import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    csrf_matches,
    new_csrf_token,
)
from kise.platform.api.dependencies import ServicesDep
from kise.platform.api.landing import STYLE_PATH, TEMPLATES_DIR
from kise.platform.container import Container
from kise.shared_kernel.domain.errors import DomainError, NotFound, TooManyRequests
from kise.shared_kernel.domain.identifiers import AdministratorId, OwnerId

# Where the portal lives. Deliberately not /admin, which every scanner probes; the real protection
# is still the admin's password, not the obscurity of the path.
PORTAL = "/kise-office"

router = APIRouter(prefix=PORTAL, include_in_schema=False)

PAGE_SIZE = 50

_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(("html",)),
    trim_blocks=True,
    lstrip_blocks=True,
)

_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'self'; style-src 'self'; img-src 'self' data:; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
}


def _container(request: Request) -> Container:
    return request.app.state.container


def _is_https(request: Request) -> bool:
    # Heroku terminates TLS at its router and says so in X-Forwarded-Proto.
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


def _client_address(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _csrf_token(request: Request) -> str:
    return request.cookies.get(CSRF_COOKIE) or new_csrf_token()


def _render(
    request: Request, template: str, *, status_code: int = 200, **context: Any
) -> HTMLResponse:
    token = _csrf_token(request)
    html = _env.get_template(f"admin/{template}").render(
        style_path=STYLE_PATH,
        portal=PORTAL,
        csrf_token=token,
        request_path=request.url.path,
        **context,
    )
    response = HTMLResponse(html, status_code=status_code, headers=_SECURITY_HEADERS)
    if request.cookies.get(CSRF_COOKIE) != token:
        response.set_cookie(
            CSRF_COOKIE,
            token,
            httponly=True,
            secure=_is_https(request),
            samesite="strict",
            path=PORTAL,
        )
    return response


def _redirect(path: str, **params: str) -> RedirectResponse:
    target = f"{path}?{urlencode(params)}" if params else path
    return RedirectResponse(target, status_code=303, headers={"Cache-Control": "no-store"})


def _signed_in_admin(request: Request, services: ServicesDep) -> AdministratorView | None:
    """The admin behind this request, or None: loaded every time, so a removed admin is out."""
    session = _container(request).admin_sessions.read(request.cookies.get(SESSION_COOKIE))
    if session is None:
        return None
    try:
        admin = services.administrators.get(session.administrator_id)
    except NotFound:
        return None
    return admin if session.still_valid_for(admin.credentials_version) else None


def _start_session(request: Request, response: Response, admin: AdministratorView) -> Response:
    sessions = _container(request).admin_sessions
    response.set_cookie(
        SESSION_COOKIE,
        sessions.issue(admin.administrator_id, admin.credentials_version),
        max_age=sessions.max_age_seconds,
        httponly=True,
        secure=_is_https(request),
        samesite="strict",
        path=PORTAL,
    )
    return response


def _csrf_ok(request: Request, submitted: str) -> bool:
    return csrf_matches(request.cookies.get(CSRF_COOKIE), submitted)


# -- sign in / out ---------------------------------------------------------------


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, services: ServicesDep) -> Response:
    if _signed_in_admin(request, services) is not None:
        return _redirect(PORTAL)
    return _login_form(request, services)


def _login_form(
    request: Request,
    services: ServicesDep,
    *,
    email: str = "",
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return _render(
        request,
        "login.html",
        status_code=status_code,
        email=email,
        error=error,
        # Until the first admin exists, say how to make one; after that, say nothing about admins.
        no_admins=not services.administrators.any_exist(),
    )


@router.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    services: ServicesDep,
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
) -> Response:
    container = _container(request)
    if not _csrf_ok(request, csrf):
        return _login_form(
            request, services, email=email, status_code=400,
            error="Your session expired. Please try again.",
        )
    try:
        container.admin_login_limiter.take(_client_address(request))
    except TooManyRequests:
        return _login_form(
            request, services, email=email, status_code=429,
            error="Too many attempts. Wait a few minutes and try again.",
        )
    try:
        admin = services.administrators.authenticate(
            AuthenticateAdministratorCommand(email=email, password=password)
        )
    except InvalidCredentials:
        # One answer for a wrong email and a wrong password: the portal does not say who its
        # admins are.
        return _login_form(
            request, services, email=email, status_code=401,
            error="That email and password do not open the admin portal.",
        )
    return _start_session(request, _redirect(PORTAL), admin)


@router.post("/logout")
def logout(request: Request, csrf: Annotated[str, Form()] = "") -> Response:
    response = _redirect(f"{PORTAL}/login")
    if _csrf_ok(request, csrf):
        response.delete_cookie(SESSION_COOKIE, path=PORTAL)
    return response


# -- pages ------------------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
def overview(
    request: Request, services: ServicesDep, saved: Annotated[str, Query()] = ""
) -> Response:
    admin = _signed_in_admin(request, services)
    if admin is None:
        return _redirect(f"{PORTAL}/login")
    return _render(
        request,
        "overview.html",
        admin=admin,
        overview=services.admin.overview(),
        max_trial_days=MAX_TRIAL_DAYS,
        saved=saved,
        error=None,
    )


@router.post("/trial", response_class=HTMLResponse)
def set_trial(
    request: Request,
    services: ServicesDep,
    trial_days: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
) -> Response:
    admin = _signed_in_admin(request, services)
    if admin is None:
        return _redirect(f"{PORTAL}/login")
    error: str | None = None
    if not _csrf_ok(request, csrf):
        error = "Your session expired. Please try again."
    else:
        try:
            services.admin.set_trial_length(
                SetTrialLengthCommand(trial_days=int(trial_days.strip()))
            )
            return _redirect(PORTAL, saved="trial")
        except ValueError:
            error = "Enter the trial length as a whole number of days."
        except DomainError as exc:
            error = exc.message
    return _render(
        request,
        "overview.html",
        status_code=422,
        admin=admin,
        overview=services.admin.overview(),
        max_trial_days=MAX_TRIAL_DAYS,
        saved="",
        error=error,
    )


@router.get("/accounts", response_class=HTMLResponse)
def accounts(
    request: Request,
    services: ServicesDep,
    q: Annotated[str, Query(max_length=100)] = "",
    page: Annotated[int, Query(ge=1)] = 1,
) -> Response:
    admin = _signed_in_admin(request, services)
    if admin is None:
        return _redirect(f"{PORTAL}/login")
    result = services.admin.accounts(
        ListAccountsQuery(text=q, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE)
    )
    pages = max(1, (result.total + PAGE_SIZE - 1) // PAGE_SIZE)
    return _render(
        request, "accounts.html", admin=admin, result=result, q=q, page=page, pages=pages
    )


def _account_page(
    request: Request,
    services: ServicesDep,
    admin: AdministratorView,
    owner_id: OwnerId,
    *,
    saved: str = "",
    error: str | None = None,
    status_code: int = 200,
) -> Response:
    try:
        account = services.admin.account(owner_id)
    except NotFound:
        return _render(request, "not_found.html", status_code=404, admin=admin)
    return _render(
        request,
        "account.html",
        status_code=status_code,
        admin=admin,
        account=account,
        plans=[plan.value for plan in Plan],
        max_grant_days=MAX_GRANT_DAYS,
        saved=saved,
        error=error,
    )


def _owner_id(raw: str) -> OwnerId | None:
    try:
        return OwnerId.parse(raw)
    except Exception:  # noqa: BLE001 - a malformed id in the URL is just "not found"
        return None


@router.get("/accounts/{owner_id}", response_class=HTMLResponse)
def account(
    request: Request,
    services: ServicesDep,
    owner_id: str,
    saved: Annotated[str, Query()] = "",
) -> Response:
    admin = _signed_in_admin(request, services)
    if admin is None:
        return _redirect(f"{PORTAL}/login")
    parsed = _owner_id(owner_id)
    if parsed is None:
        return _render(request, "not_found.html", status_code=404, admin=admin)
    return _account_page(request, services, admin, parsed, saved=saved)


@router.post("/accounts/{owner_id}/plan", response_class=HTMLResponse)
def change_plan(
    request: Request,
    services: ServicesDep,
    owner_id: str,
    plan: Annotated[str, Form()] = "",
    days: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
) -> Response:
    admin = _signed_in_admin(request, services)
    if admin is None:
        return _redirect(f"{PORTAL}/login")
    parsed = _owner_id(owner_id)
    if parsed is None:
        return _render(request, "not_found.html", status_code=404, admin=admin)
    if not _csrf_ok(request, csrf):
        return _account_page(
            request, services, admin, parsed,
            error="Your session expired. Please try again.", status_code=400,
        )
    try:
        length = int(days.strip()) if days.strip() else None
    except ValueError:
        return _account_page(
            request, services, admin, parsed,
            error="Enter the number of days as a whole number.", status_code=422,
        )
    try:
        services.admin.change_plan(ChangePlanCommand(owner_id=parsed, plan=plan, days=length))
    except NotFound:
        return _render(request, "not_found.html", status_code=404, admin=admin)
    except DomainError as exc:
        return _account_page(
            request, services, admin, parsed, error=exc.message, status_code=422
        )
    return _redirect(f"{PORTAL}/accounts/{parsed}", saved="plan")


# -- admins -----------------------------------------------------------------------


def _admins_page(
    request: Request,
    services: ServicesDep,
    admin: AdministratorView,
    *,
    saved: str = "",
    error: str | None = None,
    email: str = "",
    status_code: int = 200,
) -> Response:
    return _render(
        request,
        "admins.html",
        status_code=status_code,
        admin=admin,
        admins=services.administrators.list(),
        min_password=MIN_ADMIN_PASSWORD_LENGTH,
        saved=saved,
        error=error,
        email=email,
    )


@router.get("/admins", response_class=HTMLResponse)
def admins(
    request: Request, services: ServicesDep, saved: Annotated[str, Query()] = ""
) -> Response:
    admin = _signed_in_admin(request, services)
    if admin is None:
        return _redirect(f"{PORTAL}/login")
    return _admins_page(request, services, admin, saved=saved)


@router.post("/admins", response_class=HTMLResponse)
def add_admin(
    request: Request,
    services: ServicesDep,
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    confirm: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
) -> Response:
    admin = _signed_in_admin(request, services)
    if admin is None:
        return _redirect(f"{PORTAL}/login")
    error: str | None = None
    if not _csrf_ok(request, csrf):
        error = "Your session expired. Please try again."
    elif password != confirm:
        error = "The two passwords do not match."
    else:
        try:
            services.administrators.appoint(
                AppointAdministratorCommand(
                    email=email, password=password, appointed_by=admin.administrator_id
                )
            )
            return _redirect(f"{PORTAL}/admins", saved="added")
        except DomainError as exc:
            error = exc.message
    return _admins_page(
        request, services, admin, error=error, email=email, status_code=422
    )


@router.post("/admins/{administrator_id}/remove", response_class=HTMLResponse)
def remove_admin(
    request: Request,
    services: ServicesDep,
    administrator_id: str,
    csrf: Annotated[str, Form()] = "",
) -> Response:
    admin = _signed_in_admin(request, services)
    if admin is None:
        return _redirect(f"{PORTAL}/login")
    if not _csrf_ok(request, csrf):
        return _admins_page(
            request, services, admin,
            error="Your session expired. Please try again.", status_code=400,
        )
    try:
        target = AdministratorId.parse(administrator_id)
        services.administrators.remove(
            RemoveAdministratorCommand(administrator_id=target, removed_by=admin.administrator_id)
        )
    except NotFound:
        return _render(request, "not_found.html", status_code=404, admin=admin)
    except DomainError as exc:
        return _admins_page(request, services, admin, error=exc.message, status_code=422)
    return _redirect(f"{PORTAL}/admins", saved="removed")


# -- your password ------------------------------------------------------------------


@router.get("/password", response_class=HTMLResponse)
def password_page(
    request: Request, services: ServicesDep, saved: Annotated[str, Query()] = ""
) -> Response:
    admin = _signed_in_admin(request, services)
    if admin is None:
        return _redirect(f"{PORTAL}/login")
    return _render(
        request, "password.html", admin=admin, min_password=MIN_ADMIN_PASSWORD_LENGTH,
        saved=saved, error=None,
    )


@router.post("/password", response_class=HTMLResponse)
def change_password(
    request: Request,
    services: ServicesDep,
    current_password: Annotated[str, Form()] = "",
    new_password: Annotated[str, Form()] = "",
    confirm: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
) -> Response:
    admin = _signed_in_admin(request, services)
    if admin is None:
        return _redirect(f"{PORTAL}/login")
    error: str | None = None
    if not _csrf_ok(request, csrf):
        error = "Your session expired. Please try again."
    elif new_password != confirm:
        error = "The two new passwords do not match."
    else:
        try:
            changed = services.administrators.change_password(
                ChangeAdministratorPasswordCommand(
                    administrator_id=admin.administrator_id,
                    current_password=current_password,
                    new_password=new_password,
                )
            )
            # Every other session is now signed out; this one gets a fresh cookie.
            return _start_session(
                request, _redirect(f"{PORTAL}/password", saved="password"), changed
            )
        except DomainError as exc:
            error = exc.message
    return _render(
        request, "password.html", status_code=422, admin=admin,
        min_password=MIN_ADMIN_PASSWORD_LENGTH, saved="", error=error,
    )


# -- how people pay -------------------------------------------------------------------


def _payments_page(
    request: Request,
    admin: AdministratorView,
    form: dict[str, str],
    rows: list[tuple[str, str, str]],
    *,
    ready: bool,
    saved: str = "",
    error: str | None = None,
    status_code: int = 200,
) -> Response:
    rows = (rows + [("", "", "")] * MAX_PAYMENT_ACCOUNTS)[:MAX_PAYMENT_ACCOUNTS]
    return _render(
        request,
        "payments.html",
        status_code=status_code,
        admin=admin,
        form=form,
        rows=rows,
        ready=ready,
        saved=saved,
        error=error,
    )


def _form_of(instructions: PaymentInstructions) -> dict[str, str]:
    return {
        "price": instructions.price,
        "telegram": f"@{instructions.telegram}" if instructions.telegram else "",
        "phone": instructions.phone,
        "email": instructions.email,
        "note": instructions.note,
    }


@router.get("/payments", response_class=HTMLResponse)
def payments(
    request: Request, services: ServicesDep, saved: Annotated[str, Query()] = ""
) -> Response:
    admin = _signed_in_admin(request, services)
    if admin is None:
        return _redirect(f"{PORTAL}/login")
    current = services.admin.payment_instructions()
    return _payments_page(
        request,
        admin,
        _form_of(current),
        [(a.bank, a.holder, a.number) for a in current.accounts],
        ready=current.ready,
        saved=saved,
    )


@router.post("/payments", response_class=HTMLResponse)
def save_payments(
    request: Request,
    services: ServicesDep,
    bank: Annotated[list[str], Form()] = [],  # noqa: B006 - FastAPI copies form defaults
    holder: Annotated[list[str], Form()] = [],  # noqa: B006
    number: Annotated[list[str], Form()] = [],  # noqa: B006
    price: Annotated[str, Form()] = "",
    telegram: Annotated[str, Form()] = "",
    phone: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
) -> Response:
    admin = _signed_in_admin(request, services)
    if admin is None:
        return _redirect(f"{PORTAL}/login")
    rows = list(zip(bank, holder, number, strict=False))[:MAX_PAYMENT_ACCOUNTS]
    # A row left completely empty is simply unused; a half-filled one is an error.
    filled = tuple(row for row in rows if any(part.strip() for part in row))
    error: str | None = None
    if not _csrf_ok(request, csrf):
        error = "Your session expired. Please try again."
    else:
        try:
            services.admin.set_payment_instructions(
                SetPaymentInstructionsCommand(
                    accounts=filled,
                    price=price,
                    telegram=telegram,
                    phone=phone,
                    email=email,
                    note=note,
                )
            )
            return _redirect(f"{PORTAL}/payments", saved="payments")
        except DomainError as exc:
            error = exc.message
    # Show what was typed, not what is stored, so nothing has to be entered twice.
    typed = {"price": price, "telegram": telegram, "phone": phone, "email": email, "note": note}
    return _payments_page(
        request,
        admin,
        typed,
        list(filled),
        ready=services.admin.payment_instructions().ready,
        error=error,
        status_code=422,
    )
