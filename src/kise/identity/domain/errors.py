"""Failures the Identity context can raise."""

from __future__ import annotations

from kise.shared_kernel.domain.errors import (
    Conflict,
    DomainError,
    NotFound,
    ServiceUnavailable,
    ValidationError,
)


class OwnerNotFound(NotFound):
    code = "owner_not_found"


class EmailAlreadyRegistered(Conflict):
    code = "email_already_registered"

    def __init__(self, email: str) -> None:
        super().__init__("That email address is already registered", email=email)


class InvalidCredentials(DomainError):
    """Wrong email or wrong password — deliberately indistinguishable to the caller."""

    code = "invalid_credentials"

    def __init__(self) -> None:
        super().__init__("Email or password is incorrect")


class CurrentPasswordIncorrect(ValidationError):
    """Changing the password needs the current one; this is not a sign-in, so it is a 422."""

    code = "current_password_incorrect"

    def __init__(self) -> None:
        super().__init__("The current password is incorrect", field="current_password")


class SocialSignInFailed(DomainError):
    """The provider's token could not be verified: forged, expired, or for another app."""

    code = "social_sign_in_failed"

    def __init__(self, reason: str) -> None:
        super().__init__("That sign-in could not be confirmed. Please try again.", reason=reason)


class SocialProviderDisabled(ServiceUnavailable):
    code = "social_provider_disabled"

    def __init__(self, provider: str) -> None:
        super().__init__(f"Sign-in with {provider} is not set up on this server", provider=provider)


class SignInWithPassword(Conflict):
    """An account with that email exists, and the provider could not prove the email is theirs."""

    code = "sign_in_with_password"

    def __init__(self, email: str) -> None:
        super().__init__(
            "An account with this email already exists. Sign in with your password.",
            email=email,
        )


class InvalidToken(DomainError):
    """The bearer token is missing, malformed, or expired."""

    code = "invalid_token"


class CurrencyChangeNotAllowed(DomainError):
    code = "currency_change_not_allowed"

    def __init__(self) -> None:
        super().__init__(
            "The currency cannot be changed once expenses have been recorded, because existing "
            "amounts were written in the old currency"
        )


class AdministratorNotFound(NotFound):
    code = "administrator_not_found"


class AdministratorAlreadyExists(Conflict):
    code = "administrator_already_exists"

    def __init__(self, email: str) -> None:
        super().__init__("There is already an admin with that email", email=email)


class PlanNotFound(NotFound):
    code = "plan_not_found"


class PlanAlreadyExists(Conflict):
    code = "plan_already_exists"

    def __init__(self, code: str) -> None:
        super().__init__("There is already a plan with that name", plan=code)
