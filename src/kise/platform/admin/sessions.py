"""Admin portal sessions and CSRF tokens.

A session is an HttpOnly, SameSite=Strict cookie holding a short-lived JWT signed with the server's
``SECRET_KEY`` and a dedicated audience, so an app bearer token can never be used as a portal
session (or the other way round). It names an ``Administrator``; the router loads that admin on
every request, so removing one locks them out at once. The session also carries the admin's
credentials version, which goes up with every password change: changing a password signs out every
session issued before.

Every form carries a CSRF token, compared with a cookie of the same value (double submit). With
SameSite=Strict on both cookies that is belt and braces, but it costs nothing.
"""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from kise.shared_kernel.domain.identifiers import AdministratorId

SESSION_COOKIE = "kise_admin"
CSRF_COOKIE = "kise_admin_csrf"
_AUDIENCE = "kise-admin"
_ALGORITHM = "HS256"


@dataclass(frozen=True, slots=True)
class AdminSession:
    administrator_id: AdministratorId
    credentials_version: int

    def still_valid_for(self, credentials_version: int) -> bool:
        """False once the admin's password has changed since this session was issued."""
        return self.credentials_version == credentials_version


class AdminSessions:
    def __init__(self, secret_key: str, *, hours: int = 8) -> None:
        self._secret = secret_key
        self._lifetime = timedelta(hours=max(1, hours))

    @property
    def max_age_seconds(self) -> int:
        return int(self._lifetime.total_seconds())

    def issue(
        self,
        administrator_id: AdministratorId,
        credentials_version: int,
        now: datetime | None = None,
    ) -> str:
        issued = now or datetime.now(UTC)
        payload = {
            "sub": str(administrator_id),
            "ver": int(credentials_version),
            "aud": _AUDIENCE,
            "iat": int(issued.timestamp()),
            "exp": int((issued + self._lifetime).timestamp()),
        }
        return jwt.encode(payload, self._secret, algorithm=_ALGORITHM)

    def read(self, token: str | None) -> AdminSession | None:
        """The session in the cookie, or None for a missing, forged or expired one."""
        if not token:
            return None
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[_ALGORITHM],
                audience=_AUDIENCE,
                options={"require": ["sub", "ver", "exp"]},
            )
            return AdminSession(
                administrator_id=AdministratorId.parse(str(payload["sub"])),
                credentials_version=int(payload["ver"]),
            )
        except Exception:  # noqa: BLE001 - anything unreadable is just "no session"
            return None


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_matches(cookie: str | None, submitted: str | None) -> bool:
    return bool(cookie and submitted) and hmac.compare_digest(cookie, submitted)
