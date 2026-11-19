"""Ports the Identity application layer depends on.

They exist so that no rule ever contains a hashing algorithm, a token format, or a provider's
key-verification protocol. The domain holds a
``PasswordHash`` value object and nothing else about credentials.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from kise.identity.domain.models import PasswordHash, SocialProvider
from kise.shared_kernel.domain.identifiers import OwnerId


class PasswordHasher(ABC):
    """Turns a plaintext password into a hash, and checks one against a hash."""

    @abstractmethod
    def hash(self, plaintext: str) -> PasswordHash:
        """Hash a password. Implementations must salt."""

    @abstractmethod
    def verify(self, plaintext: str, hashed: PasswordHash) -> bool:
        """Whether the password matches. Must not raise on a malformed hash."""


@dataclass(frozen=True, slots=True)
class AccessToken:
    """A bearer token and when it stops working."""

    value: str
    expires_at: datetime

    def __str__(self) -> str:  # pragma: no cover - keep tokens out of logs
        return "<access token>"

    __repr__ = __str__


class TokenService(ABC):
    """Issues and reads bearer tokens."""

    @abstractmethod
    def issue(self, owner_id: OwnerId) -> AccessToken:
        """Mint a token identifying this Owner."""

    @abstractmethod
    def read(self, token: str) -> OwnerId:
        """Return the Owner the token identifies, or raise ``InvalidToken``."""


@dataclass(frozen=True, slots=True)
class VerifiedSocialAccount:
    """What a provider vouches for, after its token has been checked."""

    provider: SocialProvider
    subject: str
    email: str | None = None
    #: True only when the provider says it has confirmed the address belongs to this person.
    email_verified: bool = False
    name: str | None = None


class SocialVerifier(ABC):
    """Checks a token from Google, Apple or Facebook and says whose account it is."""

    @abstractmethod
    def enabled(self) -> set[SocialProvider]:
        """The providers this server is configured for."""

    @abstractmethod
    def verify(
        self, provider: SocialProvider, token: str, *, nonce: str | None = None
    ) -> VerifiedSocialAccount:
        """The account behind ``token``.

        Raises ``SocialSignInFailed`` for a bad token, ``SocialProviderDisabled`` if not set up.
        """
