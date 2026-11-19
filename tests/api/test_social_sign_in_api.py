"""Social sign-in over HTTP, with a scripted verifier standing in for Google, Apple and Facebook.

What they prove:

* The first sign-in makes an account (``created: true``), seeded with the default categories, in the
  calendar/language/currency the app sent; the next sign-in with the same provider account returns
  the same Owner.
* An existing email+password account is linked only when the provider verified the email; a
  Facebook sign-in for an existing email is told to use the password instead.
* A Facebook account that shares no email still gets an account.
* Bad tokens are 401, unconfigured providers 503, and a social-only account cannot be entered with
  an empty or guessed password.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kise.identity.application.ports import SocialVerifier, VerifiedSocialAccount
from kise.identity.domain.errors import SocialProviderDisabled, SocialSignInFailed
from kise.identity.domain.models import SocialProvider
from kise.platform.api.app import create_app
from kise.platform.config import Settings
from kise.platform.container import Container

SECRET = "a-secret-long-enough-to-be-taken-seriously-xxxx"


class ScriptedVerifier(SocialVerifier):
    """Token text -> account, for the providers in ``on``."""

    def __init__(self, on: set[SocialProvider] | None = None) -> None:
        self.on = on if on is not None else set(SocialProvider)
        self.accounts: dict[str, VerifiedSocialAccount] = {}
        self.nonces: list[str | None] = []

    def enabled(self) -> set[SocialProvider]:
        return set(self.on)

    def verify(self, provider, token, *, nonce=None):
        if provider not in self.on:
            raise SocialProviderDisabled(provider.value)
        self.nonces.append(nonce)
        account = self.accounts.get(token)
        if account is None or account.provider is not provider:
            raise SocialSignInFailed("token_invalid")
        return account


@pytest.fixture
def verifier() -> ScriptedVerifier:
    v = ScriptedVerifier()
    v.accounts["g-abebe"] = VerifiedSocialAccount(
        SocialProvider.GOOGLE, "g-1", "abebe@gmail.com", True, "Abebe Kebede"
    )
    v.accounts["a-abebe"] = VerifiedSocialAccount(
        SocialProvider.APPLE, "a-1", "abebe@gmail.com", True
    )
    v.accounts["fb-abebe"] = VerifiedSocialAccount(
        SocialProvider.FACEBOOK, "f-1", "abebe@gmail.com", False, "Abebe"
    )
    v.accounts["fb-noemail"] = VerifiedSocialAccount(
        SocialProvider.FACEBOOK, "f-2", None, False, "Hana"
    )
    return v


@pytest.fixture
def client(verifier: ScriptedVerifier) -> TestClient:
    settings = Settings(
        _env_file=None, database_url="sqlite://", secret_key=SECRET, default_currency="ETB"
    )
    container = Container(settings, social_verifier=verifier)
    container.create_schema()
    return TestClient(create_app(settings, container=container))


def social(client: TestClient, provider: str, token: str, **extra):
    return client.post("/api/auth/social", json={"provider": provider, "token": token, **extra})


def test_providers_endpoint_reports_what_is_configured(verifier, client):
    verifier.on = {SocialProvider.GOOGLE}
    assert client.get("/api/auth/providers").json() == {
        "password": True,
        "google": True,
        "apple": False,
        "facebook": False,
    }


def test_first_sign_in_creates_the_account_and_the_next_one_finds_it(client: TestClient):
    first = social(client, "google", "g-abebe", calendar="gregorian", language="en")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["created"] is True
    assert body["owner"]["email"] == "abebe@gmail.com"
    assert body["owner"]["display_name"] == "Abebe Kebede"
    assert body["owner"]["calendar"] == "gregorian"
    header = {"Authorization": f"Bearer {body['token']}"}
    assert len(client.get("/api/categories", headers=header).json()) == 10  # seeded

    again = social(client, "google", "g-abebe").json()
    assert again["created"] is False
    assert again["owner"]["id"] == body["owner"]["id"]


def test_apple_passes_the_nonce_and_the_name_from_the_app(client, verifier):
    response = social(client, "apple", "a-abebe", nonce="n-123", display_name="Abebe K")
    assert response.status_code == 200, response.text
    assert response.json()["owner"]["display_name"] == "Abebe K"
    assert verifier.nonces == ["n-123"]


def test_a_verified_email_links_to_the_existing_password_account(client: TestClient):
    registered = client.post(
        "/api/auth/register",
        json={"email": "abebe@gmail.com", "password": "correct horse battery", "display_name": "A"},
    ).json()
    linked = social(client, "google", "g-abebe").json()
    assert linked["created"] is False
    assert linked["owner"]["id"] == registered["owner"]["id"]
    # And Apple, with the same verified email, links to the same account too.
    assert social(client, "apple", "a-abebe", nonce="n").json()["owner"]["id"] == (
        registered["owner"]["id"]
    )


def test_an_unverified_email_never_takes_over_an_existing_account(client: TestClient):
    client.post(
        "/api/auth/register",
        json={"email": "abebe@gmail.com", "password": "correct horse battery", "display_name": "A"},
    )
    response = social(client, "facebook", "fb-abebe")
    assert response.status_code == 409
    assert response.json()["code"] == "sign_in_with_password"


def test_facebook_without_an_email_still_gets_an_account(client: TestClient):
    body = social(client, "facebook", "fb-noemail").json()
    assert body["created"] is True
    assert body["owner"]["display_name"] == "Hana"
    assert body["owner"]["email"].endswith("@users.kise.invalid")
    # Stable: the same Facebook account comes back to the same Owner.
    assert social(client, "facebook", "fb-noemail").json()["owner"]["id"] == body["owner"]["id"]


def test_bad_tokens_and_unconfigured_providers(client: TestClient, verifier):
    bad = social(client, "google", "forged")
    assert bad.status_code == 401
    assert bad.json()["code"] == "social_sign_in_failed"

    verifier.on = set()
    off = social(client, "google", "g-abebe")
    assert off.status_code == 503
    assert off.json()["code"] == "social_provider_disabled"

    unknown = social(client, "myspace", "x")
    assert unknown.status_code == 422


def test_a_social_account_has_no_usable_password(client: TestClient):
    social(client, "google", "g-abebe")
    for password in ("", "        ", "correct horse battery"):
        response = client.post(
            "/api/auth/login", json={"email": "abebe@gmail.com", "password": password}
        )
        assert response.status_code == 401
