"""Mapping contracts for the Identity context.

These are **ports**: the specification of how an aggregate is translated to and from storage. The
SQLAlchemy that actually does it lives in ``identity/infrastructure/persistence/mappers.py``, and a
contract test asserts that implementation satisfies the protocol declared here.

The domain therefore states *what* must be translatable without knowing what a row looks like — the
model type is left open on purpose. Same reasoning as ``OwnerRepository``: the vocabulary belongs to
the model, the mechanism does not.
"""

from kise.identity.domain.mappers.administrator_mapping import AdministratorMapping
from kise.identity.domain.mappers.linked_identity_mapping import LinkedIdentityMapping
from kise.identity.domain.mappers.owner_mapping import OwnerMapping
from kise.identity.domain.mappers.plan_definition_mapping import PlanDefinitionMapping

__all__ = [
    "AdministratorMapping",
    "LinkedIdentityMapping",
    "OwnerMapping",
    "PlanDefinitionMapping",
]
