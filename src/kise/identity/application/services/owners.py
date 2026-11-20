"""The Identity application service.

One service per aggregate, and it is the only thing the presentation layer talks to. A router calls
``OwnerService.register(...)`` and gets back a plain result; it never sees a repository, a mapper or
a row.

The service is the transaction boundary. Each public method is one business operation: it resolves
the primitives it was handed into value objects, asks the aggregate to do the work, and commits.
Rules stay in the aggregate — this layer coordinates, it does not decide.
"""

from __future__ import annotations

import hashlib
import secrets

from kise.identity.application.commands import (
    AuthenticateOwnerCommand,
    ChangePasswordCommand,
    RegisterOwnerCommand,
    SocialSignInCommand,
    UpdatePreferencesCommand,
)
from kise.identity.application.ports import (
    PasswordHasher,
    SocialVerifier,
    TokenService,
    VerifiedSocialAccount,
)
from kise.identity.application.services.plans import PlanCatalogue
from kise.identity.application.views import (
    AuthenticatedOwner,
    OwnerProfile,
)
from kise.identity.domain.errors import (
    CurrentPasswordIncorrect,
    EmailAlreadyRegistered,
    InvalidCredentials,
    SignInWithPassword,
    SocialProviderDisabled,
)
from kise.identity.domain.models import (
    MAX_DISPLAY_NAME_LENGTH,
    EmailAddress,
    LinkedIdentity,
    Owner,
    PlanPolicy,
    Preferences,
    SocialProvider,
    Subscription,
)
from kise.identity.infrastructure.persistence.repository import (
    SqlAlchemyLinkedIdentityRepository,
    SqlAlchemyOwnerRepository,
    SqlAlchemyPlanRepository,
    SqlAlchemyPlanSettingsRepository,
)
from kise.shared_kernel.application.ports import Clock, UnitOfWork
from kise.shared_kernel.domain.errors import InvariantViolation
from kise.shared_kernel.domain.identifiers import OwnerId


class OwnerService:
    """Everything a person can do to their own account."""

    def __init__(
        self,
        owners: SqlAlchemyOwnerRepository,
        hasher: PasswordHasher,
        tokens: TokenService,
        clock: Clock,
        uow: UnitOfWork,
        *,
        linked_identities: SqlAlchemyLinkedIdentityRepository | None = None,
        social: SocialVerifier | None = None,
        plan_settings: SqlAlchemyPlanSettingsRepository | None = None,
        plans: SqlAlchemyPlanRepository | None = None,
    ) -> None:
        self._catalogue = PlanCatalogue(plans) if plans is not None else None
        self._plan_settings = plan_settings
        self._linked = linked_identities
        self._social = social
        self._owners = owners
        self._hasher = hasher
        self._tokens = tokens
        self._clock = clock
        self._uow = uow

    # -- commands -------------------------------------------------------
    def register(self, command: RegisterOwnerCommand) -> AuthenticatedOwner:
        """Create an account and sign in, so registering is one round trip."""
        email = EmailAddress(command.email)
        # Checked here rather than left to the unique index, so the caller gets a sentence instead
        # of a constraint name. The index is still the last word.
        if self._owners.email_exists(email):
            raise EmailAlreadyRegistered(str(email))

        preferences = Preferences(
            **{
                key: value
                for key, value in (
                    ("calendar", command.calendar),
                    ("language", command.language),
                    ("currency", command.currency),
                )
                if value is not None
            }
        )
        owner = Owner.register(
            email=email,
            password_hash=self._hasher.hash(command.password),
            display_name=command.display_name,
            registered_at=self._clock.now(),
            preferences=preferences,
            subscription=self._new_account_subscription(),
        )
        self._owners.add(owner)
        # Committing publishes OwnerRegistered, which is what seeds the default categories.
        self._uow.commit()
        return AuthenticatedOwner(self._profile(owner), self._tokens.issue(owner.id))

    def authenticate(self, command: AuthenticateOwnerCommand) -> AuthenticatedOwner:
        """Sign in. A wrong password and an unknown account give the same answer, on purpose."""
        try:
            email = EmailAddress(command.email)
        except Exception as exc:  # noqa: BLE001 - a malformed email is just a failed sign-in
            raise InvalidCredentials from exc

        owner = self._owners.find_by_email(email)
        if owner is None or not self._hasher.verify(command.password, owner.password_hash):
            raise InvalidCredentials
        return AuthenticatedOwner(self._profile(owner), self._tokens.issue(owner.id))

    def enabled_social_providers(self) -> set[SocialProvider]:
        return self._social.enabled() if self._social is not None else set()

    def sign_in_with_social(self, command: SocialSignInCommand) -> AuthenticatedOwner:
        """Sign in with Google, Apple or Facebook — making the account on first use.

        1. The token is verified by the provider's rules (signature, audience, expiry).
        2. A linked account signs straight in.
        3. Otherwise, an existing Owner with the same email is linked — but only when the provider
           has *confirmed* that email (Google and Apple say so explicitly). Without that, anyone
           who could set an email on a Facebook account could take over a Kise account, so the
           person is asked to sign in with their password instead.
        4. Otherwise a new Owner is registered, which seeds the default categories as usual.
        """
        provider = SocialProvider.parse(command.provider)
        if self._social is None or self._linked is None:
            raise SocialProviderDisabled(provider.value)
        account = self._social.verify(provider, command.token, nonce=command.nonce)

        linked = self._linked.find(provider, account.subject)
        if linked is not None:
            owner = self._owners.get(linked.owner_id)
            return AuthenticatedOwner(self._profile(owner), self._tokens.issue(owner.id))

        email = self._email_for(account)
        existing = self._owners.find_by_email(email)
        if existing is not None:
            if not (account.email_verified and account.email):
                raise SignInWithPassword(str(email))
            self._link(existing, account)
            self._uow.commit()
            return AuthenticatedOwner(self._profile(existing), self._tokens.issue(existing.id))

        preferences = Preferences(
            **{
                key: value
                for key, value in (
                    ("calendar", command.calendar),
                    ("language", command.language),
                    ("currency", command.currency),
                )
                if value is not None
            }
        )
        owner = Owner.register(
            email=email,
            # No password: the account is reached through the provider. A random secret nobody
            # knows fills the slot, so password sign-in fails cleanly rather than matching "".
            password_hash=self._hasher.hash(secrets.token_urlsafe(32)),
            display_name=self._display_name(command.display_name, account, email),
            registered_at=self._clock.now(),
            preferences=preferences,
            subscription=self._new_account_subscription(),
        )
        self._owners.add(owner)
        self._link(owner, account)
        self._uow.commit()
        return AuthenticatedOwner(
            self._profile(owner), self._tokens.issue(owner.id), created=True
        )

    def _new_account_subscription(self) -> Subscription:
        """What a new account starts on, by the admin-set ``PlanPolicy``."""
        policy = self._plan_settings.get() if self._plan_settings is not None else PlanPolicy()
        return policy.new_account_subscription(self._clock.now())

    def _link(self, owner: Owner, account: VerifiedSocialAccount) -> None:
        assert self._linked is not None  # checked by the caller
        self._linked.add(
            LinkedIdentity.link(
                owner_id=owner.id,
                provider=account.provider,
                subject=account.subject,
                email=account.email,
                linked_at=self._clock.now(),
            )
        )

    @staticmethod
    def _email_for(account: VerifiedSocialAccount) -> EmailAddress:
        """The provider's email, or — when it shares none (Facebook can withhold it) — a stand-in
        under a domain Kise controls, unique to that provider account and never mailed."""
        if account.email:
            try:
                return EmailAddress(account.email)
            except Exception:  # noqa: BLE001 - a malformed provider email is just "no email"
                pass
        digest = hashlib.sha256(f"{account.provider.value}:{account.subject}".encode()).hexdigest()
        return EmailAddress(f"{account.provider.value}-{digest[:24]}@users.kise.invalid")

    @staticmethod
    def _display_name(
        given: str | None, account: VerifiedSocialAccount, email: EmailAddress
    ) -> str:
        for candidate in (given, account.name):
            cleaned = " ".join((candidate or "").split())
            if cleaned:
                return cleaned[:MAX_DISPLAY_NAME_LENGTH]
        local = str(email).split("@", 1)[0]
        return local[:MAX_DISPLAY_NAME_LENGTH] if "users.kise.invalid" not in str(email) else "Kise"

    def update_preferences(self, command: UpdatePreferencesCommand) -> OwnerProfile:
        """Change the display name, calendar or language.

        Currency is not here: changing it is only safe before anything has been recorded, which is
        knowledge this service does not have. See ``Owner.change_currency``.
        """
        owner = self._owners.get(command.owner_id)
        if command.display_name is not None:
            owner.rename(command.display_name)
        if command.calendar is not None:
            owner.prefer_calendar(command.calendar)
        if command.language is not None:
            owner.prefer_language(command.language)
        self._uow.commit()
        return self._profile(owner)

    def change_password(self, command: ChangePasswordCommand) -> None:
        """Replace the password, proving the current one first.

        Tokens already issued stay valid until they expire: Kise v1 has no revocation list.
        """
        owner = self._owners.get(command.owner_id)
        if not self._hasher.verify(command.current_password, owner.password_hash):
            raise CurrentPasswordIncorrect
        # Hashes are salted, so "same password" must be checked against the plain text.
        if self._hasher.verify(command.new_password, owner.password_hash):
            raise InvariantViolation("The new password must differ from the current one")
        owner.change_password(self._hasher.hash(command.new_password))
        self._uow.commit()

    # -- queries --------------------------------------------------------
    def profile(self, owner_id: OwnerId) -> OwnerProfile:
        return self._profile(self._owners.get(owner_id))

    def owner_for_token(self, token: str) -> OwnerProfile:
        """Resolve a bearer token to a profile. Used by the API's auth dependency."""
        return self.profile(self._tokens.read(token))

    # -- internals ------------------------------------------------------
    def _profile(self, owner: Owner) -> OwnerProfile:
        now = self._clock.now()
        plan = self._catalogue.in_force(owner.subscription, now) if self._catalogue else None
        return OwnerProfile.of(owner, now, plan)

