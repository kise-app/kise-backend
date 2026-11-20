"""The PlanDefinition mapping contract."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from kise.identity.domain.models import PlanDefinition


@runtime_checkable
class PlanDefinitionMapping(Protocol):
    @staticmethod
    def to_model(aggregate: PlanDefinition) -> Any: ...

    @staticmethod
    def to_domain(model: Any) -> PlanDefinition: ...

    @staticmethod
    def update_model(model: Any, aggregate: PlanDefinition) -> None: ...
