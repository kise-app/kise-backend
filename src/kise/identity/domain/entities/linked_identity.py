"""LinkedIdentity — a Google, Apple or Facebook account an Owner can sign in with.

The provider's own id for the person (``subject``, the ``sub`` claim) is the key, not the email:
emails change and Apple hands out private relay addresses, but the subject is stable for the life of
the account. One provider account belongs to exactly one Owner; one Owner may link several.

Its own aggregate rather than a child of ``Owner``, because the question asked at sign-in is "who
owns this Google account?" — a lookup by (provider, subject) that should not load every Owner.
"""

from __future__ import annotations

from datetime import datetime

from kise.identity.domain.value_objects import SocialProvider
from kise.shared_kernel.domain.entity import AggregateRoot
from kise.shared_kernel.domain.errors import ValidationError
from kise.shared_kernel.domain.identifiers import LinkedIdentityId, OwnerId

MAX_SUBJECT_LENGTH = 255


class LinkedIdentity(AggregateRoot):
    def __init__(
        self,
        identity_id: LinkedIdentityId,
        *,
        owner_id: OwnerId,
        provider: SocialProvider | str,
        subject: str,
        email: str | None,
        linked_at: datetime,
    ) -> None:
        super().__init__(identity_id)
        cleaned = (subject or "").strip()
        if not cleaned or len(cleaned) > MAX_SUBJECT_LENGTH:
            raise ValidationError("A linked account needs the provider's id", field="subject")
        self._owner_id = owner_id
        self._provider = SocialProvider.parse(provider)
        self._subject = cleaned
        self._email = email.strip().lower() if email else None
        self._linked_at = linked_at

    @classmethod
    def link(
        cls,
        *,
        owner_id: OwnerId,
        provider: SocialProvider | str,
        subject: str,
        email: str | None,
        linked_at: datetime,
    ) -> LinkedIdentity:
        return cls(
            LinkedIdentityId.new(),
            owner_id=owner_id,
            provider=provider,
            subject=subject,
            email=email,
            linked_at=linked_at,
        )

    @property
    def id(self) -> LinkedIdentityId:
        return self._id  # type: ignore[return-value]

    @property
    def owner_id(self) -> OwnerId:
        return self._owner_id

    @property
    def provider(self) -> SocialProvider:
        return self._provider

    @property
    def subject(self) -> str:
        return self._subject

    @property
    def email(self) -> str | None:
        """The email the provider reported when it was linked — for display, never as a key."""
        return self._email

    @property
    def linked_at(self) -> datetime:
        return self._linked_at
