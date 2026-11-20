"""Administrator — someone who can sign in to the admin portal.

Not an ``Owner``: an administrator has no expenses, no plan and no app sign-in, and an Owner never
gains portal access by having the right email. Keeping them apart means a leaked app password
cannot open the portal, and the portal's credentials never travel through the phone app.

The first administrator is created from the command line (``created_by`` is None); after that,
administrators add each other in the portal.
"""

from __future__ import annotations

from datetime import datetime

from kise.identity.domain.value_objects import EmailAddress, PasswordHash
from kise.shared_kernel.domain.entity import AggregateRoot
from kise.shared_kernel.domain.errors import InvariantViolation, ValidationError
from kise.shared_kernel.domain.identifiers import AdministratorId


class Administrator(AggregateRoot):
    def __init__(
        self,
        administrator_id: AdministratorId,
        *,
        email: EmailAddress,
        password_hash: PasswordHash,
        created_at: datetime,
        created_by: AdministratorId | None,
        password_changed_at: datetime,
        credentials_version: int = 1,
    ) -> None:
        super().__init__(administrator_id)
        if not isinstance(email, EmailAddress):
            raise ValidationError("email must be an EmailAddress")
        if not isinstance(password_hash, PasswordHash):
            raise ValidationError("password_hash must be a PasswordHash")
        self._email = email
        self._password_hash = password_hash
        self._created_at = created_at
        self._created_by = created_by
        self._password_changed_at = password_changed_at
        self._credentials_version = credentials_version

    @classmethod
    def appoint(
        cls,
        *,
        email: EmailAddress,
        password_hash: PasswordHash,
        now: datetime,
        created_by: AdministratorId | None = None,
    ) -> Administrator:
        """A new administrator. ``created_by`` is None only for one made at the command line."""
        return cls(
            AdministratorId.new(),
            email=email,
            password_hash=password_hash,
            created_at=now,
            created_by=created_by,
            password_changed_at=now,
        )

    # -- state ----------------------------------------------------------
    @property
    def id(self) -> AdministratorId:
        return self._id  # type: ignore[return-value]

    @property
    def email(self) -> EmailAddress:
        return self._email

    @property
    def password_hash(self) -> PasswordHash:
        return self._password_hash

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def created_by(self) -> AdministratorId | None:
        return self._created_by

    @property
    def password_changed_at(self) -> datetime:
        return self._password_changed_at

    @property
    def credentials_version(self) -> int:
        """Goes up with every password change; a session made for an older version is over."""
        return self._credentials_version

    # -- behaviour ------------------------------------------------------
    def change_password(self, password_hash: PasswordHash, *, now: datetime) -> None:
        if not isinstance(password_hash, PasswordHash):
            raise ValidationError("password_hash must be a PasswordHash")
        self._password_hash = password_hash
        self._password_changed_at = now
        self._credentials_version += 1

    def ensure_removable_by(self, remover: AdministratorId) -> None:
        """An administrator cannot remove themselves, so the portal is never left with nobody."""
        if remover == self.id:
            raise InvariantViolation("You cannot remove yourself. Ask another admin to do it.")
