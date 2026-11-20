"""The Administrator aggregate and the admin password rule."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kise.identity.domain.models import (
    Administrator,
    EmailAddress,
    PasswordHash,
    check_new_admin_password,
)
from kise.shared_kernel.domain.errors import InvariantViolation, ValidationError
from kise.shared_kernel.domain.identifiers import AdministratorId

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)


def appoint(created_by: AdministratorId | None = None) -> Administrator:
    return Administrator.appoint(
        email=EmailAddress("Admin@Kise.example"),
        password_hash=PasswordHash("hash-1"),
        now=NOW,
        created_by=created_by,
    )


def test_appointing_records_who_and_when():
    first = appoint()
    second = appoint(created_by=first.id)
    assert str(first.email) == "admin@kise.example"
    assert first.created_by is None, "made at the command line"
    assert second.created_by == first.id
    assert first.created_at == first.password_changed_at == NOW
    assert first.credentials_version == 1


def test_changing_the_password_moves_the_credentials_version_on():
    admin = appoint()
    later = NOW + timedelta(days=3)
    admin.change_password(PasswordHash("hash-2"), now=later)
    assert admin.password_hash == PasswordHash("hash-2")
    assert admin.password_changed_at == later
    assert admin.credentials_version == 2


def test_nobody_removes_themselves():
    admin = appoint()
    with pytest.raises(InvariantViolation, match="cannot remove yourself"):
        admin.ensure_removable_by(admin.id)
    admin.ensure_removable_by(AdministratorId.new())


@pytest.mark.parametrize("password", ["", "eleven char", "x" * 73, "é" * 37])
def test_admin_passwords_must_be_long_but_fit_bcrypt(password):
    with pytest.raises(ValidationError):
        check_new_admin_password(password)


def test_a_good_admin_password_is_returned_unchanged():
    assert check_new_admin_password("twelve chars") == "twelve chars"
