"""Wire schemas for the auth endpoints, and the mapping to and from the application layer.

Pydantic stops here. A request model becomes a command (``to_command``); a view becomes a response
model (``ProfileResponse.of``). The application layer sees only its own commands and views, and the
domain sees neither — which is why a malformed email is refused in the OwnerService, not by a
Pydantic validator pretending to know the domain rule.
"""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field

from kise.identity.application.commands import (
    AuthenticateOwnerCommand,
    ChangePasswordCommand,
    RegisterOwnerCommand,
    SocialSignInCommand,
    UpdatePreferencesCommand,
)
from kise.identity.application.views import AuthenticatedOwner, OwnerProfile


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    display_name: str = Field(min_length=1, max_length=120)
    calendar: str | None = Field(default=None, examples=["ethiopian", "gregorian"])
    language: str | None = Field(default=None, examples=["am", "en"])
    currency: str | None = Field(default=None, examples=["ETB", "USD"])

    def to_command(self) -> RegisterOwnerCommand:
        return RegisterOwnerCommand(
            email=str(self.email),
            password=self.password,
            display_name=self.display_name,
            calendar=self.calendar,
            language=self.language,
            currency=self.currency,
        )


class SocialSignInRequest(BaseModel):
    provider: str = Field(examples=["google", "apple", "facebook"])
    token: str = Field(
        min_length=1,
        max_length=8192,
        description="Google/Apple: the ID token. Facebook: the access token.",
    )
    nonce: str | None = Field(
        default=None, max_length=256, description="Apple: the raw nonce whose SHA-256 was sent"
    )
    display_name: str | None = Field(default=None, max_length=120)
    calendar: str | None = None
    language: str | None = None
    currency: str | None = None

    def to_command(self) -> SocialSignInCommand:
        return SocialSignInCommand(
            provider=self.provider,
            token=self.token,
            nonce=self.nonce,
            display_name=self.display_name,
            calendar=self.calendar,
            language=self.language,
            currency=self.currency,
        )


class ProvidersResponse(BaseModel):
    """Which sign-in methods this server offers. Email and password is always on."""

    password: bool = True
    google: bool
    apple: bool
    facebook: bool


class LoginRequest(BaseModel):
    email: EmailStr
    password: str

    def to_command(self) -> AuthenticateOwnerCommand:
        return AuthenticateOwnerCommand(email=str(self.email), password=self.password)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=72)

    def to_command(self, owner_id: object) -> ChangePasswordCommand:
        return ChangePasswordCommand(
            owner_id=owner_id,  # type: ignore[arg-type]  # OwnerId, passed opaque by the router
            current_password=self.current_password,
            new_password=self.new_password,
        )


class UpdateProfileRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    calendar: str | None = Field(default=None, examples=["ethiopian", "gregorian"])
    language: str | None = Field(default=None, examples=["am", "en"])

    def to_command(self, owner_id: object) -> UpdatePreferencesCommand:
        return UpdatePreferencesCommand(
            owner_id=owner_id,  # type: ignore[arg-type]  # OwnerId, passed opaque by the router
            display_name=self.display_name,
            calendar=self.calendar,
            language=self.language,
        )


class ProfileResponse(BaseModel):
    id: str
    email: str
    display_name: str
    calendar: str
    language: str
    currency: str
    registered_at: str

    @classmethod
    def of(cls, profile: OwnerProfile) -> ProfileResponse:
        return cls(
            id=str(profile.owner_id),
            email=profile.email,
            display_name=profile.display_name,
            calendar=profile.calendar.value,
            language=profile.language.value,
            currency=profile.currency,
            registered_at=profile.registered_at.isoformat(),
        )


class AuthResponse(BaseModel):
    """A profile plus the token that proves it — the body of register and login."""

    token: str
    expires_at: str
    owner: ProfileResponse
    #: True when the sign-in created the account.
    created: bool = False

    @classmethod
    def of(cls, authenticated: AuthenticatedOwner) -> AuthResponse:
        return cls(
            token=authenticated.token.value,
            expires_at=authenticated.token.expires_at.isoformat(),
            owner=ProfileResponse.of(authenticated.profile),
            created=authenticated.created,
        )
