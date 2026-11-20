"""What the Identity service accepts.

Commands carry primitives, because they arrive from outside — an HTTP body, a CLI argument, a test.
Turning those primitives into value objects is the service's first job, so a malformed email is
refused at the edge of the application rather than deep inside a rule.

What the service *returns* lives in ``views.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

from kise.shared_kernel.domain.identifiers import AdministratorId, OwnerId


@dataclass(frozen=True, slots=True)
class RegisterOwnerCommand:
    email: str
    password: str
    display_name: str
    calendar: str | None = None
    language: str | None = None
    currency: str | None = None


@dataclass(frozen=True, slots=True)
class AuthenticateOwnerCommand:
    email: str
    password: str


@dataclass(frozen=True, slots=True)
class SocialSignInCommand:
    """Sign in (or register) with a provider's token.

    ``display_name`` matters for Apple, which sends the person's name only to the app and only the
    first time; ``calendar``/``language``/``currency`` are used only when a new account is made.
    """

    provider: str
    token: str
    nonce: str | None = None
    display_name: str | None = None
    calendar: str | None = None
    language: str | None = None
    currency: str | None = None


@dataclass(frozen=True, slots=True)
class ChangePasswordCommand:
    owner_id: OwnerId
    current_password: str
    new_password: str


@dataclass(frozen=True, slots=True)
class UpdatePreferencesCommand:
    owner_id: OwnerId
    display_name: str | None = None
    calendar: str | None = None
    language: str | None = None


# -- account administration --------------------------------------------------


@dataclass(frozen=True, slots=True)
class ListAccountsQuery:
    """Accounts, newest first; ``text`` matches the email or the name."""

    text: str = ""
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True, slots=True)
class ChangePlanCommand:
    owner_id: OwnerId
    plan: str
    #: Length of a trial or of a time-limited Pro grant. None: Basic, or Pro without an end.
    days: int | None = None


@dataclass(frozen=True, slots=True)
class SetTrialLengthCommand:
    trial_days: int


# -- administrators -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AppointAdministratorCommand:
    """Add an admin. ``appointed_by`` is the admin doing it; None only at the command line."""

    email: str
    password: str
    appointed_by: AdministratorId | None = None


@dataclass(frozen=True, slots=True)
class AuthenticateAdministratorCommand:
    email: str
    password: str


@dataclass(frozen=True, slots=True)
class ChangeAdministratorPasswordCommand:
    administrator_id: AdministratorId
    current_password: str
    new_password: str


@dataclass(frozen=True, slots=True)
class ResetAdministratorPasswordCommand:
    """Set an admin's password without the current one. Command line only: for a forgotten one."""

    email: str
    new_password: str


@dataclass(frozen=True, slots=True)
class RemoveAdministratorCommand:
    administrator_id: AdministratorId
    removed_by: AdministratorId


@dataclass(frozen=True, slots=True)
class SetPaymentInstructionsCommand:
    """How to pay for Pro. ``accounts`` are (bank, account name, account number) triples."""

    accounts: tuple[tuple[str, str, str], ...] = ()
    price: str = ""
    telegram: str = ""
    phone: str = ""
    email: str = ""
    note: str = ""
