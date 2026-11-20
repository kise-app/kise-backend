"""Entities of the Identity context — identity by id, mutable, rule-carrying.

Aggregate roots: ``Owner``; ``LinkedIdentity`` (a Google, Apple or Facebook account an Owner
signs in with); ``Administrator`` (someone who can sign in to the admin portal). Value objects live
next door in ``value_objects.py``.
"""

from kise.identity.domain.entities.administrator import Administrator
from kise.identity.domain.entities.linked_identity import LinkedIdentity
from kise.identity.domain.entities.owner import Owner

__all__ = ["Administrator", "LinkedIdentity", "Owner"]
