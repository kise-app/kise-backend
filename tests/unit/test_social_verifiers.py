"""Google, Apple and Facebook token verification, with no network.

ID tokens are signed here with a throwaway RSA key and the verifier is given the matching public
key, so every check runs for real: signature, audience, issuer, expiry and Apple's nonce.
Facebook's Graph API is a scripted transport.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from types import SimpleNamespace

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from kise.identity.domain.errors import SocialProviderDisabled, SocialSignInFailed
from kise.identity.domain.models import SocialProvider
from kise.identity.infrastructure.social import (
    AppleVerifier,
    FacebookVerifier,
    GoogleVerifier,
    ProviderSocialVerifier,
)

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class Keys:
    def get_signing_key_from_jwt(self, token: str):
        return SimpleNamespace(key=KEY.public_key())


def id_token(key=KEY, **claims) -> str:
    now = int(time.time())
    payload = {
        "iss": "https://accounts.google.com",
        "aud": "ios-client.apps.googleusercontent.com",
        "sub": "google-123",
        "iat": now,
        "exp": now + 600,
        "email": "abebe@gmail.com",
        "email_verified": True,
        "name": "Abebe Kebede",
        **claims,
    }
    return jwt.encode(payload, key, algorithm="RS256")


def google() -> GoogleVerifier:
    return GoogleVerifier(
        ["ios-client.apps.googleusercontent.com", "server.apps.googleusercontent.com"],
        keys=Keys(),
    )


# -- Google -----------------------------------------------------------------


def test_google_accepts_a_good_token():
    account = google().verify(id_token())
    assert account.provider is SocialProvider.GOOGLE
    assert account.subject == "google-123"
    assert account.email == "abebe@gmail.com"
    assert account.email_verified is True
    assert account.name == "Abebe Kebede"


def test_google_accepts_any_configured_client_id_and_the_short_issuer():
    token = id_token(aud="server.apps.googleusercontent.com", iss="accounts.google.com")
    assert google().verify(token).subject == "google-123"


@pytest.mark.parametrize(
    ("token", "reason"),
    [
        (id_token(aud="someone-elses-app"), "wrong_audience"),
        (id_token(iss="https://evil.example"), "wrong_issuer"),
        (id_token(exp=int(time.time()) - 3600), "token_expired"),
        (id_token(key=OTHER_KEY), "token_invalid"),
        ("not.a.jwt", "token_invalid"),
    ],
)
def test_google_refuses_bad_tokens(token, reason):
    with pytest.raises(SocialSignInFailed) as caught:
        google().verify(token)
    assert caught.value.detail["reason"] == reason


def test_an_unverified_google_email_is_reported_as_such():
    assert google().verify(id_token(email_verified=False)).email_verified is False


# -- Apple ------------------------------------------------------------------


def apple_token(nonce: str = "raw-nonce", **claims) -> str:
    return id_token(
        iss="https://appleid.apple.com",
        aud="com.example.kise",
        sub="apple-001",
        email="abc@privaterelay.appleid.com",
        email_verified="true",  # Apple has sent it as a string
        nonce=hashlib.sha256(nonce.encode()).hexdigest(),
        name=None,
        **claims,
    )


def apple() -> AppleVerifier:
    return AppleVerifier(["com.example.kise"], keys=Keys())


def test_apple_accepts_a_good_token_with_its_nonce():
    account = apple().verify(apple_token(), "raw-nonce")
    assert account.subject == "apple-001"
    assert account.email_verified is True


@pytest.mark.parametrize(
    ("nonce", "reason"), [(None, "nonce_missing"), ("other", "nonce_mismatch")]
)
def test_apple_requires_the_matching_nonce(nonce, reason):
    with pytest.raises(SocialSignInFailed) as caught:
        apple().verify(apple_token(), nonce)
    assert caught.value.detail["reason"] == reason


def test_a_google_token_is_not_an_apple_token():
    with pytest.raises(SocialSignInFailed):
        apple().verify(id_token(), "raw-nonce")


# -- Facebook ---------------------------------------------------------------


def facebook(handler) -> tuple[FacebookVerifier, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return FacebookVerifier("app-1", "app-secret", transport=httpx.MockTransport(record)), seen


def graph(debug: dict, me: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/debug_token":
            return httpx.Response(200, json={"data": debug})
        return httpx.Response(200, json=me)

    return handler


def test_facebook_accepts_a_token_issued_to_this_app():
    verifier, seen = facebook(
        graph(
            {"is_valid": True, "app_id": "app-1", "user_id": "fb-9"},
            {"id": "fb-9", "name": "Selam", "email": "selam@example.com"},
        )
    )
    account = verifier.verify("user-access-token")
    assert account.subject == "fb-9"
    assert account.email == "selam@example.com"
    assert account.email_verified is False, "Facebook emails never link existing accounts"
    debug, me = seen
    assert debug.url.params["access_token"] == "app-1|app-secret"
    expected_proof = hmac.new(b"app-secret", b"user-access-token", hashlib.sha256).hexdigest()
    assert me.url.params["appsecret_proof"] == expected_proof


@pytest.mark.parametrize(
    ("debug", "reason"),
    [
        ({"is_valid": False}, "token_invalid"),
        ({"is_valid": True, "app_id": "another-app", "user_id": "fb-9"}, "wrong_audience"),
    ],
)
def test_facebook_refuses_tokens_for_other_apps_or_invalid_ones(debug, reason):
    verifier, _ = facebook(graph(debug, {"id": "fb-9"}))
    with pytest.raises(SocialSignInFailed) as caught:
        verifier.verify("t")
    assert caught.value.detail["reason"] == reason


def test_facebook_errors_from_the_graph_api():
    verifier, _ = facebook(lambda r: httpx.Response(400, json={"error": {"message": "bad"}}))
    with pytest.raises(SocialSignInFailed) as caught:
        verifier.verify("t")
    assert caught.value.detail["reason"] == "provider_http_400"


# -- routing ----------------------------------------------------------------


def test_only_configured_providers_are_enabled():
    verifier = ProviderSocialVerifier(
        google=google(),
        apple=AppleVerifier([], keys=Keys()),  # no client id: off
        facebook=FacebookVerifier("", ""),
    )
    assert verifier.enabled() == {SocialProvider.GOOGLE}
    with pytest.raises(SocialProviderDisabled):
        verifier.verify(SocialProvider.APPLE, apple_token(), nonce="raw-nonce")
    assert verifier.verify(SocialProvider.GOOGLE, id_token()).subject == "google-123"
