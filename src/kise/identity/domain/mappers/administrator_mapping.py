"""The Administrator mapping contract."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from kise.identity.domain.models import Administrator


@runtime_checkable
class AdministratorMapping(Protocol):
    @staticmethod
    def to_model(aggregate: Administrator) -> Any: ...

    @staticmethod
    def to_domain(model: Any) -> Administrator: ...

    @staticmethod
    def update_model(model: Any, aggregate: Administrator) -> None: ...
