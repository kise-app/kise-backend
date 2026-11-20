"""Administrators: who can sign in to the admin portal.

The first one is appointed at the command line (``python -m kise.platform.admin create``), which is
also where a forgotten password is reset. After that, admins appoint and remove each other in the
portal and change their own passwords there. Like every service here it coordinates: the rules —
what an admin password must be, that nobody removes themselves — live in the domain.
"""

from __future__ import annotations

import secrets

from kise.identity.application.commands import (
    AppointAdministratorCommand,
    AuthenticateAdministratorCommand,
    ChangeAdministratorPasswordCommand,
    RemoveAdministratorCommand,
    ResetAdministratorPasswordCommand,
)
from kise.identity.application.ports import PasswordHasher
from kise.identity.application.views import AdministratorView
from kise.identity.domain.errors import (
    AdministratorAlreadyExists,
    AdministratorNotFound,
    CurrentPasswordIncorrect,
    InvalidCredentials,
)
from kise.identity.domain.models import (
    Administrator,
    EmailAddress,
    PasswordHash,
    check_new_admin_password,
)
from kise.identity.infrastructure.persistence.repository import SqlAlchemyAdministratorRepository
from kise.shared_kernel.application.ports import Clock, UnitOfWork
from kise.shared_kernel.domain.errors import InvariantViolation, ValidationError
from kise.shared_kernel.domain.identifiers import AdministratorId


class AdministratorService:
    #: A hash of a random secret, checked when the email is unknown so that a wrong email takes as
    #: long as a wrong password and the timing does not reveal who the admins are.
    _decoy: PasswordHash | None = None

    def __init__(
        self,
        administrators: SqlAlchemyAdministratorRepository,
        hasher: PasswordHasher,
        clock: Clock,
        uow: UnitOfWork,
    ) -> None:
        self._admins = administrators
        self._hasher = hasher
        self._clock = clock
        self._uow = uow

    def _view(self, admin: Administrator) -> AdministratorView:
        creator = None
        if admin.created_by is not None:
            try:
                creator = str(self._admins.get(admin.created_by).email)
            except AdministratorNotFound:
                creator = None
        return AdministratorView.of(admin, creator)

    def _hash_new(self, password: str) -> PasswordHash:
        return self._hasher.hash(check_new_admin_password(password))

    # -- queries ------------------------------------------------------------
    def any_exist(self) -> bool:
        return self._admins.count() > 0

    def get(self, administrator_id: AdministratorId) -> AdministratorView:
        return self._view(self._admins.get(administrator_id))

    def list(self) -> list[AdministratorView]:
        return [self._view(admin) for admin in self._admins.list()]

    # -- commands -------------------------------------------------------------
    def appoint(self, command: AppointAdministratorCommand) -> AdministratorView:
        email = EmailAddress(command.email)
        if self._admins.find_by_email(email) is not None:
            raise AdministratorAlreadyExists(str(email))
        if command.appointed_by is not None:
            self._admins.get(command.appointed_by)  # the appointing admin must still exist
        admin = Administrator.appoint(
            email=email,
            password_hash=self._hash_new(command.password),
            now=self._clock.now(),
            created_by=command.appointed_by,
        )
        self._admins.add(admin)
        self._uow.commit()
        return self._view(admin)

    def authenticate(self, command: AuthenticateAdministratorCommand) -> AdministratorView:
        """Sign in. A wrong password and an unknown email give the same answer, on purpose."""
        try:
            email = EmailAddress(command.email)
        except ValidationError:
            email = None
        admin = self._admins.find_by_email(email) if email is not None else None
        if admin is None:
            self._hasher.verify(command.password, self._decoy_hash())
            raise InvalidCredentials
        if not self._hasher.verify(command.password, admin.password_hash):
            raise InvalidCredentials
        return self._view(admin)

    def change_password(self, command: ChangeAdministratorPasswordCommand) -> AdministratorView:
        admin = self._admins.get(command.administrator_id)
        if not self._hasher.verify(command.current_password, admin.password_hash):
            raise CurrentPasswordIncorrect
        # Hashes are salted, so "same password" must be checked against the plain text.
        if command.new_password == command.current_password:
            raise InvariantViolation("The new password must differ from the current one")
        admin.change_password(self._hash_new(command.new_password), now=self._clock.now())
        self._uow.commit()
        return self._view(admin)

    def reset_password(self, command: ResetAdministratorPasswordCommand) -> AdministratorView:
        admin = self._admins.find_by_email(EmailAddress(command.email))
        if admin is None:
            raise AdministratorNotFound("No admin has that email", email=command.email)
        admin.change_password(self._hash_new(command.new_password), now=self._clock.now())
        self._uow.commit()
        return self._view(admin)

    def remove(self, command: RemoveAdministratorCommand) -> None:
        admin = self._admins.get(command.administrator_id)
        admin.ensure_removable_by(command.removed_by)
        self._admins.remove(admin)
        self._uow.commit()

    def _decoy_hash(self) -> PasswordHash:
        if AdministratorService._decoy is None:
            AdministratorService._decoy = self._hasher.hash(secrets.token_urlsafe(32))
        return AdministratorService._decoy
