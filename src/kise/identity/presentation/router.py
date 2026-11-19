"""The auth router: register, login, and the signed-in owner's profile.

Every handler is thin by design. It maps the request to a command, calls the one service method,
and maps the result back to a response — no rules, no persistence, no branching on domain state.
Domain errors raised by the service (``EmailAlreadyRegistered``, ``InvalidCredentials``) propagate
to the single error handler, which turns them into 409 / 401.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from kise.identity.presentation.schemas import (
    AuthResponse,
    ChangePasswordRequest,
    LoginRequest,
    ProfileResponse,
    ProvidersResponse,
    RegisterRequest,
    SocialSignInRequest,
    UpdateProfileRequest,
)
from kise.platform.api.dependencies import CurrentOwner, ServicesDep

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(request: RegisterRequest, services: ServicesDep) -> AuthResponse:
    """Create an account and sign in, so registering is one round trip.

    Committing publishes ``OwnerRegistered``, which seeds the ten default categories.
    """
    authenticated = services.owners.register(request.to_command())
    return AuthResponse.of(authenticated)


@router.post("/login", response_model=AuthResponse)
def login(request: LoginRequest, services: ServicesDep) -> AuthResponse:
    """Sign in. A wrong password and an unknown account give the same 401, on purpose."""
    authenticated = services.owners.authenticate(request.to_command())
    return AuthResponse.of(authenticated)


@router.get("/providers", response_model=ProvidersResponse)
def providers(services: ServicesDep) -> ProvidersResponse:
    """Which sign-in buttons the app should show."""
    enabled = {p.value for p in services.owners.enabled_social_providers()}
    return ProvidersResponse(
        google="google" in enabled, apple="apple" in enabled, facebook="facebook" in enabled
    )


@router.post("/social", response_model=AuthResponse)
def social_sign_in(request: SocialSignInRequest, services: ServicesDep) -> AuthResponse:
    """Sign in with Google, Apple or Facebook; the first time makes the account (``created``).

    401 ``social_sign_in_failed`` when the token does not check out, 409 ``sign_in_with_password``
    when an account with that email exists and the provider did not verify the email, 503
    ``social_provider_disabled`` when the provider is not set up here.
    """
    return AuthResponse.of(services.owners.sign_in_with_social(request.to_command()))


@router.get("/me", response_model=ProfileResponse)
def me(current_owner: CurrentOwner) -> ProfileResponse:
    """The signed-in owner's profile. ``get_current_owner`` already resolved the token."""
    return ProfileResponse.of(current_owner)


@router.patch("/me", response_model=ProfileResponse)
def update_me(
    request: UpdateProfileRequest, current_owner: CurrentOwner, services: ServicesDep
) -> ProfileResponse:
    """Change display name, calendar or language. Currency is deliberately not editable here."""
    profile = services.owners.update_preferences(request.to_command(current_owner.owner_id))
    return ProfileResponse.of(profile)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    request: ChangePasswordRequest, current_owner: CurrentOwner, services: ServicesDep
) -> Response:
    """Change the password. A wrong current password is a 422 ``current_password_incorrect``."""
    services.owners.change_password(request.to_command(current_owner.owner_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
