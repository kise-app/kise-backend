"""Value objects of the Identity context — compared by value, immutable, self-validating."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum

from kise.shared_kernel.domain.calendar.calendar_kind import CalendarKind, Language
from kise.shared_kernel.domain.errors import ValidationError
from kise.shared_kernel.domain.money import DEFAULT_CURRENCY

MAX_DISPLAY_NAME_LENGTH = 60
MAX_EMAIL_LENGTH = 254

# Deliberately permissive: the domain rejects what is obviously not an address and leaves
# deliverability to reality. Over-strict email regexes reject valid addresses.
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")


@dataclass(frozen=True, slots=True)
class EmailAddress:
    """An email address, normalised to lower case so it can serve as a unique key."""

    value: str

    def __post_init__(self) -> None:
        raw = self.value
        if not isinstance(raw, str):
            raise ValidationError("Email address must be text", field="email")
        cleaned = raw.strip().lower()
        if not cleaned:
            raise ValidationError("Email address is required", field="email")
        if len(cleaned) > MAX_EMAIL_LENGTH:
            raise ValidationError("Email address is too long", field="email")
        if not _EMAIL_PATTERN.match(cleaned):
            raise ValidationError(f"{raw!r} is not a valid email address", field="email")
        if cleaned != raw:
            object.__setattr__(self, "value", cleaned)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class PasswordHash:
    """An already-hashed password.

    The domain never hashes and never sees a plaintext password — that is the ``PasswordHasher``
    port's job. This type exists so a raw password can never be stored by accident.
    """

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value.strip():
            raise ValidationError("Password hash is required", field="password_hash")

    def __str__(self) -> str:  # pragma: no cover - avoid leaking hashes into logs
        return "<password hash>"

    __repr__ = __str__


#: Admin portal passwords: the same minimum as app passwords. Guessing is held back by the sign-in
#: throttle (10 tries per 15 minutes per address). The upper bound is bcrypt's: it ignores
#: everything past 72 bytes.
MIN_ADMIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_BYTES = 72


def check_new_admin_password(plaintext: str) -> str:
    """Refuse an admin password too short or too long to be safe; return it unchanged.

    It is checked here and then hashed straight away; the plain text is never kept.
    """
    if not isinstance(plaintext, str) or len(plaintext) < MIN_ADMIN_PASSWORD_LENGTH:
        raise ValidationError(
            f"Use at least {MIN_ADMIN_PASSWORD_LENGTH} characters for an admin password",
            field="password",
        )
    if len(plaintext.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValidationError("That password is too long", field="password")
    return plaintext


@dataclass(frozen=True, slots=True)
class Preferences:
    """How the Owner wants Kise presented: which calendar, which language, which currency."""

    calendar: CalendarKind = CalendarKind.ETHIOPIAN
    language: Language = Language.AMHARIC
    currency: str = DEFAULT_CURRENCY

    def __post_init__(self) -> None:
        calendar = CalendarKind.parse(self.calendar)
        if calendar is not self.calendar:
            object.__setattr__(self, "calendar", calendar)
        language = Language.parse(self.language)
        if language is not self.language:
            object.__setattr__(self, "language", language)
        code = self.currency
        if not isinstance(code, str) or len(code) != 3 or not code.isalpha():
            raise ValidationError(
                f"Currency must be three letters, got {self.currency!r}", field="currency"
            )
        if not code.isupper():
            object.__setattr__(self, "currency", code.upper())

    def with_calendar(self, calendar: CalendarKind | str) -> Preferences:
        return replace(self, calendar=CalendarKind.parse(calendar))

    def with_language(self, language: Language | str) -> Preferences:
        return replace(self, language=Language.parse(language))

    def with_currency(self, currency: str) -> Preferences:
        return replace(self, currency=currency)


def clean_display_name(raw: str) -> str:
    if not isinstance(raw, str):
        raise ValidationError("Display name must be text", field="display_name")
    cleaned = " ".join(raw.split())
    if not cleaned:
        raise ValidationError("Display name is required", field="display_name")
    if len(cleaned) > MAX_DISPLAY_NAME_LENGTH:
        raise ValidationError(
            f"Display name must be at most {MAX_DISPLAY_NAME_LENGTH} characters",
            field="display_name",
        )
    return cleaned


class SocialProvider(str, Enum):
    """The sign-in providers Kise accepts besides email and password."""

    GOOGLE = "google"
    APPLE = "apple"
    FACEBOOK = "facebook"

    @classmethod
    def parse(cls, value: SocialProvider | str) -> SocialProvider:
        if isinstance(value, SocialProvider):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError as exc:
            raise ValidationError(
                f"Unknown sign-in provider {value!r}", field="provider"
            ) from exc


class Plan(str, Enum):
    """What an account is entitled to.

    ``trial``  everything, for a limited time — then Basic
    ``basic``  everything except the AI features
    ``pro``    everything, including voice, receipt scans and questions; may have an end date
    """

    TRIAL = "trial"
    BASIC = "basic"
    PRO = "pro"

    @classmethod
    def parse(cls, value: Plan | str) -> Plan:
        if isinstance(value, Plan):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError as exc:
            raise ValidationError(f"Unknown plan {value!r}", field="plan") from exc


#: Plans whose holders may use the assistant (voice, receipts, questions).
AI_PLANS = frozenset({Plan.TRIAL, Plan.PRO})

#: The longest trial an admin may set for new accounts.
MAX_TRIAL_DAYS = 365
#: The longest a time-limited grant (a trial, or Pro for N days) may run.
MAX_GRANT_DAYS = 3660


def _whole_days(days: object, *, low: int, high: int, field: str) -> int:
    if isinstance(days, bool) or not isinstance(days, int) or not low <= days <= high:
        raise ValidationError(f"{field} must be a whole number from {low} to {high}", field=field)
    return days


@dataclass(frozen=True, slots=True)
class Subscription:
    """A plan and when it stops — the one place the entitlement rules live.

    A trial always ends; Pro may end (a paid month) or not (granted indefinitely); Basic never
    ends. A plan past its end is simply Basic, so nothing has to run at midnight to "expire" it.
    """

    plan: Plan = Plan.BASIC
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        plan = Plan.parse(self.plan)
        if plan is not self.plan:
            object.__setattr__(self, "plan", plan)
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValidationError("A plan's end must carry a timezone", field="expires_at")
        if plan is Plan.TRIAL and self.expires_at is None:
            raise ValidationError("A trial needs an end date", field="expires_at")
        if plan is Plan.BASIC and self.expires_at is not None:
            object.__setattr__(self, "expires_at", None)

    @classmethod
    def trial_until(cls, ends_at: datetime) -> Subscription:
        return cls(Plan.TRIAL, ends_at)

    @classmethod
    def grant(cls, plan: Plan | str, *, now: datetime, days: int | None = None) -> Subscription:
        """Put an account on ``plan`` from ``now``.

        Basic takes no length. A trial needs one. Pro runs ``days`` days, or without an end when
        no length is given (an indefinite grant).
        """
        plan = Plan.parse(plan)
        if plan is Plan.BASIC:
            return cls()
        if days is None:
            if plan is Plan.TRIAL:
                raise ValidationError("A trial needs a number of days", field="days")
            return cls(Plan.PRO)
        length = _whole_days(days, low=1, high=MAX_GRANT_DAYS, field="days")
        return cls(plan, now + timedelta(days=length))

    def effective(self, now: datetime) -> Plan:
        """The plan in force at ``now``: an ended trial or Pro period is Basic."""
        if self.expires_at is not None and now >= self.expires_at:
            return Plan.BASIC
        return self.plan

    def has_ai(self, now: datetime) -> bool:
        return self.effective(now) in AI_PLANS

    def days_left(self, now: datetime) -> int | None:
        """Days until the plan ends, counting a part day as a day — a 14-day trial says 14 on the
        day it starts and 1 on its last — or None when it does not end."""
        if self.expires_at is None or self.effective(now) is Plan.BASIC:
            return None
        remaining = (self.expires_at - now).total_seconds()
        return max(1, math.ceil(remaining / 86400))


@dataclass(frozen=True, slots=True)
class PlanPolicy:
    """The plan rules an admin can tune. Today: how long new accounts' trial runs.

    ``trial_days = 0`` means new accounts start on Basic.
    """

    trial_days: int = 14

    def __post_init__(self) -> None:
        _whole_days(self.trial_days, low=0, high=MAX_TRIAL_DAYS, field="trial_days")

    def new_account_subscription(self, now: datetime) -> Subscription:
        if self.trial_days == 0:
            return Subscription()
        return Subscription.trial_until(now + timedelta(days=self.trial_days))


# -- paying for Pro --------------------------------------------------------------
#
# There is no payment provider: a person pays into one of Kise's accounts (a bank or a mobile wallet
# such as telebirr) and sends the receipt screenshot on Telegram, and an admin then moves them to
# Pro. ``PaymentInstructions`` is what the app shows them; an admin edits it in the portal.

MAX_PAYMENT_ACCOUNTS = 5
_TELEGRAM_USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")
_TELEGRAM_PREFIXES = ("https://t.me/", "http://t.me/", "t.me/", "https://telegram.me/", "@")
_PHONE_PATTERN = re.compile(r"^\+?[0-9][0-9 \-]{5,22}[0-9]$")


def _text(value: object, *, field: str, label: str, limit: int, required: bool = False) -> str:
    cleaned = " ".join(str(value or "").split())
    if required and not cleaned:
        raise ValidationError(f"{label} is required", field=field)
    if len(cleaned) > limit:
        raise ValidationError(f"{label} is too long (at most {limit} characters)", field=field)
    return cleaned


@dataclass(frozen=True, slots=True)
class PaymentAccount:
    """One place to pay into: a bank account or a mobile wallet number."""

    bank: str
    holder: str
    number: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "bank", _text(self.bank, field="bank", label="Bank", limit=60, required=True)
        )
        object.__setattr__(
            self,
            "holder",
            _text(self.holder, field="holder", label="Account name", limit=80, required=True),
        )
        object.__setattr__(
            self,
            "number",
            _text(self.number, field="number", label="Account number", limit=40, required=True),
        )


def clean_telegram_username(raw: str) -> str:
    """``@kise_support``, ``kise_support`` or ``https://t.me/kise_support`` -> ``kise_support``."""
    cleaned = (raw or "").strip()
    for prefix in _TELEGRAM_PREFIXES:
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    cleaned = cleaned.strip("/")
    if cleaned and not _TELEGRAM_USERNAME.match(cleaned):
        raise ValidationError(
            "Enter a Telegram username like @kise_support (5 to 32 letters, digits or _)",
            field="telegram",
        )
    return cleaned


def clean_phone(raw: str) -> str:
    cleaned = " ".join((raw or "").split())
    if cleaned and not _PHONE_PATTERN.match(cleaned):
        raise ValidationError("Enter a phone number like +251 911 234 567", field="phone")
    return cleaned


@dataclass(frozen=True, slots=True)
class PaymentInstructions:
    """How to pay for Pro: where to send the money and how to tell Kise it was sent.

    ``ready`` is False until there is somewhere to pay *and* a way to send the screenshot; until
    then the app says "contact us" without showing a half-finished set of details.
    """

    accounts: tuple[PaymentAccount, ...] = ()
    #: What each paid plan costs, as free text ("150 birr a month", "1,500 birr a year"). Empty
    #: means no price is shown for that plan. Trial has no price: an admin sets its length.
    basic_price: str = ""
    pro_price: str = ""
    telegram: str = ""
    phone: str = ""
    email: str = ""
    #: Anything else to say, e.g. "We switch you to Pro within a day."
    note: str = ""

    def __post_init__(self) -> None:
        accounts = tuple(self.accounts)
        if len(accounts) > MAX_PAYMENT_ACCOUNTS:
            raise ValidationError(
                f"At most {MAX_PAYMENT_ACCOUNTS} payment accounts", field="accounts"
            )
        if not all(isinstance(account, PaymentAccount) for account in accounts):
            raise ValidationError("accounts must be PaymentAccount values", field="accounts")
        object.__setattr__(self, "accounts", accounts)
        for field, label in (("basic_price", "Basic price"), ("pro_price", "Pro price")):
            object.__setattr__(
                self, field, _text(getattr(self, field), field=field, label=label, limit=120)
            )
        object.__setattr__(self, "telegram", clean_telegram_username(self.telegram))
        object.__setattr__(self, "phone", clean_phone(self.phone))
        object.__setattr__(self, "email", str(EmailAddress(self.email)) if self.email else "")
        object.__setattr__(self, "note", _text(self.note, field="note", label="Note", limit=500))

    @property
    def ready(self) -> bool:
        return bool(self.accounts) and bool(self.telegram or self.phone or self.email)

    def price_of(self, plan: Plan | str) -> str:
        """The price shown for ``plan``; empty for Trial, or a plan without one."""
        return {Plan.BASIC: self.basic_price, Plan.PRO: self.pro_price}.get(Plan.parse(plan), "")

    @property
    def telegram_url(self) -> str | None:
        return f"https://t.me/{self.telegram}" if self.telegram else None
