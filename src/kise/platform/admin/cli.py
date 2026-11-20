"""Create the first admin, or reset a forgotten admin password, from the command line.

    python -m kise.platform.admin create            # asks for the email and the password
    python -m kise.platform.admin reset-password    # asks for the email and a new password

On Heroku: ``heroku run "PYTHONPATH=src python -m kise.platform.admin create"`` (or
``make heroku-create-admin``); Heroku installs the dependencies but not this package. The password
is typed, never passed as an argument, so it stays out of shell history and process lists. Anyone
who can run this already controls the server, so it is allowed at any time; after the first admin,
the portal is the usual way to add others.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Callable, Sequence

from kise.identity.application.commands import (
    AppointAdministratorCommand,
    ResetAdministratorPasswordCommand,
)
from kise.identity.domain.models import MIN_ADMIN_PASSWORD_LENGTH
from kise.platform.config import get_settings
from kise.platform.container import Container, build_container
from kise.shared_kernel.domain.errors import DomainError

PORTAL_HINT = "Sign in at /kise-office/login."


def _ask_password(secret: Callable[[str], str], out: Callable[[str], None]) -> str | None:
    password = secret(f"Password (at least {MIN_ADMIN_PASSWORD_LENGTH} characters): ")
    if secret("Repeat the password: ") != password:
        out("The two passwords do not match. Nothing was changed.")
        return None
    return password


def main(
    argv: Sequence[str] | None = None,
    *,
    container: Container | None = None,
    prompt: Callable[[str], str] = input,
    secret: Callable[[str], str] = getpass.getpass,
    out: Callable[[str], None] = print,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m kise.platform.admin")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("create", help="create an admin (the first one, usually)")
    commands.add_parser("reset-password", help="set a new password for an existing admin")
    args = parser.parse_args(argv)

    container = container or build_container(get_settings())
    uow = container.open_unit_of_work()
    try:
        administrators = container.build_services(uow).administrators
        email = prompt("Admin email: ").strip()
        password = _ask_password(secret, out)
        if password is None:
            return 1
        if args.command == "create":
            created = administrators.appoint(
                AppointAdministratorCommand(email=email, password=password)
            )
            out(f"Admin {created.email} created. {PORTAL_HINT}")
        else:
            changed = administrators.reset_password(
                ResetAdministratorPasswordCommand(email=email, new_password=password)
            )
            out(f"Password for {changed.email} changed; their other sessions are signed out.")
        return 0
    except DomainError as exc:
        out(f"Nothing was changed: {exc.message}")
        return 1
    finally:
        uow.session.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
