"""The LinkedIdentity mapping contract."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from kise.identity.domain.models import LinkedIdentity

__all__ = ["LinkedIdentityMapping"]


@runtime_checkable
class LinkedIdentityMapping(Protocol):
    @staticmethod
    def to_model(aggregate: LinkedIdentity) -> Any: ...

    @staticmethod
    def to_domain(model: Any) -> LinkedIdentity: ...

    @staticmethod
    def update_model(model: Any, aggregate: LinkedIdentity) -> None: ...
