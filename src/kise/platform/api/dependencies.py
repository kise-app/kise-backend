"""FastAPI dependencies — the seam between HTTP and the application layer.

A router never constructs a service or reads a token itself. It declares what it needs as a
dependency, and this module hands it over, bound to a Unit of Work that lives exactly as long as the
request. Three things are provided:

* ``get_services`` — every application service for one request, sharing one Unit of Work, so a
  route that touches two of them still commits atomically. The Unit of Work is rolled back if the
  route raised, then always closed.
* ``get_current_owner`` — resolves the ``Authorization: Bearer <jwt>`` header to an ``OwnerProfile``
  by asking the OwnerService. A missing or malformed token becomes ``InvalidToken``, which the error
  handler renders as 401.
* ``get_owner_services`` — the same, built for the signed-in owner, so money is written in *their*
  currency. Every expense-tracking route uses this one.
* ``get_assistant`` — the assistant use cases for the signed-in owner, with their language,
  calendar and currency, over that owner's spending.
* ``get_container`` — the singletons, for the rare dependency that needs them directly.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from kise.assistant.application.service import AssistantRequest, AssistantService
from kise.identity.application.views import OwnerProfile
from kise.identity.domain.errors import InvalidToken
from kise.platform.assistant_bridge import ExpenseTrackingSpending
from kise.platform.container import Container, RequestServices

# auto_error=False: a missing header must become our ``InvalidToken`` (with the domain error shape),
# not FastAPI's default 403 with its own body.
_bearer = HTTPBearer(auto_error=False)


def get_container(request: Request) -> Container:
    """The application container, stored on ``app.state`` by the factory."""
    return request.app.state.container


def get_services(request: Request) -> Iterator[RequestServices]:
    """Per-request services sharing one Unit of Work; rolled back on error, always closed."""
    container: Container = request.app.state.container
    uow = container.open_unit_of_work()
    try:
        yield container.build_services(uow)
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.session.close()


ServicesDep = Annotated[RequestServices, Depends(get_services)]


def get_current_owner(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> OwnerProfile:
    """Resolve the bearer token to the signed-in owner, or raise ``InvalidToken`` (401)."""
    if credentials is None or not credentials.credentials:
        raise InvalidToken("Missing bearer token")
    container: Container = request.app.state.container
    uow, service = container.owner_service_for_token()
    try:
        return service.owner_for_token(credentials.credentials)
    finally:
        uow.session.close()


CurrentOwner = Annotated[OwnerProfile, Depends(get_current_owner)]


def get_owner_services(request: Request, owner: CurrentOwner) -> Iterator[RequestServices]:
    """Per-request services for the signed-in owner: as ``get_services``, in the owner's currency.

    FastAPI resolves ``CurrentOwner`` once per request, so a route that also declares it does not
    decode the token twice.
    """
    container: Container = request.app.state.container
    uow = container.open_unit_of_work()
    try:
        yield container.build_services(uow, currency=owner.currency)
    except Exception:
        uow.rollback()
        raise
    finally:
        uow.session.close()


OwnerServicesDep = Annotated[RequestServices, Depends(get_owner_services)]


def get_assistant(
    request: Request, owner: CurrentOwner, services: OwnerServicesDep
) -> tuple[AssistantService, AssistantRequest]:
    """The assistant for the signed-in owner, reading their spending through ``services``."""
    container: Container = request.app.state.container
    service = AssistantService(
        model=container.language_model,
        spending=ExpenseTrackingSpending(services),
        limiter=container.rate_limiter,
        clock=container.clock,
    )
    return service, AssistantRequest(
        owner_id=owner.owner_id,
        language=owner.language.value,
        calendar=owner.calendar,
        currency=owner.currency,
        allowed=owner.ai_enabled,
    )


AssistantDep = Annotated[tuple[AssistantService, AssistantRequest], Depends(get_assistant)]


def get_assistant_status(request: Request, owner: CurrentOwner) -> tuple[bool, int, bool]:
    """(switched on for this server, hourly allowance, this owner's plan includes it)."""
    container: Container = request.app.state.container
    return (
        container.assistant_enabled,
        container.settings.assistant_hourly_limit,
        owner.ai_enabled,
    )


AssistantStatusDep = Annotated[tuple[bool, int, bool], Depends(get_assistant_status)]
