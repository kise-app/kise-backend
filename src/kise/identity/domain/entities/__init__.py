"""Entities of the Identity context — identity by id, mutable, rule-carrying.

Aggregate roots: ``Owner``, and ``LinkedIdentity`` (a Google, Apple or Facebook account an Owner
signs in with). Value objects live next door in ``value_objects.py``.
"""

from kise.identity.domain.entities.linked_identity import LinkedIdentity
from kise.identity.domain.entities.owner import Owner

__all__ = ["LinkedIdentity", "Owner"]
