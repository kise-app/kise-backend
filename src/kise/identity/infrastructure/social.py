"""Verifying sign-in tokens from Google, Apple and Facebook.

The app does the provider's sign-in flow and sends Kise what it got back. Kise trusts none of it
until it has checked it the provider's way:

* **Google** and **Apple** return an *ID token*: a JWT signed with keys the provider publishes
  (JWKS). It is valid only if the signature matches one of those keys, the issuer is the provider,
  the audience is one of *this* app's client ids, and it has not expired. That last audience check
  is what stops a token minted for some other app from signing in here.
* **Apple** additionally carries the SHA-256 of a one-time ``nonce`` the app generated, so a
  captured token cannot be replayed.
* **Facebook** returns an *access token*, which is opaque: it is checked by asking the Graph API's
  ``debug_token`` (with this app's own credentials) whether it is valid and was issued to this app,
  then the profile is fetched with an ``appsecret_proof``.

Email handling is deliberately conservative: only Google's and Apple's explicit
``email_verified`` makes an email *verified*. Facebook's is used for display but never trusted to
link an existing account.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Sequence
from typing import Any, Protocol

import httpx
import jwt

from kise.identity.application.ports import SocialVerifier, VerifiedSocialAccount
from kise.identity.domain.errors import SocialProviderDisabled, SocialSignInFailed
from kise.identity.domain.models import SocialProvider

GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
APPLE_ISSUER = "https://appleid.apple.com"
FACEBOOK_GRAPH_URL = "https://graph.facebook.com"


class SigningKeySource(Protocol):
    """What ``jwt.PyJWKClient`` provides; a fake in tests."""

    def get_signing_key_from_jwt(self, token: str) -> Any: ...


def _truthy(value: Any) -> bool:
    # Apple has sent email_verified both as a boolean and as the string "true".
    return value is True or (isinstance(value, str) and value.lower() == "true")


class _IdTokenVerifier:
    """Shared ID-token checks for Google and Apple."""

    def __init__(
        self,
        *,
        provider: SocialProvider,
        client_ids: Sequence[str],
        issuers: Sequence[str],
        keys: SigningKeySource,
    ) -> None:
        self._provider = provider
        self._client_ids = [c for c in client_ids if c]
        self._issuers = set(issuers)
        self._keys = keys

    @property
    def configured(self) -> bool:
        return bool(self._client_ids)

    def claims(self, token: str) -> dict[str, Any]:
        try:
            key = self._keys.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self._client_ids,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
                leeway=60,
            )
        except jwt.ExpiredSignatureError as exc:
            raise SocialSignInFailed("token_expired") from exc
        except jwt.InvalidAudienceError as exc:
            raise SocialSignInFailed("wrong_audience") from exc
        except jwt.PyJWKClientError as exc:
            raise SocialSignInFailed("signing_key_unavailable") from exc
        except jwt.InvalidTokenError as exc:
            raise SocialSignInFailed("token_invalid") from exc
        if claims.get("iss") not in self._issuers:
            raise SocialSignInFailed("wrong_issuer")
        return claims


class GoogleVerifier(_IdTokenVerifier):
    def __init__(self, client_ids: Sequence[str], *, keys: SigningKeySource | None = None) -> None:
        super().__init__(
            provider=SocialProvider.GOOGLE,
            client_ids=client_ids,
            issuers=GOOGLE_ISSUERS,
            keys=keys or jwt.PyJWKClient(GOOGLE_JWKS_URL, cache_keys=True, lifespan=3600),
        )

    def verify(self, token: str) -> VerifiedSocialAccount:
        claims = self.claims(token)
        return VerifiedSocialAccount(
            provider=SocialProvider.GOOGLE,
            subject=str(claims["sub"]),
            email=claims.get("email"),
            email_verified=_truthy(claims.get("email_verified")),
            name=claims.get("name"),
        )


class AppleVerifier(_IdTokenVerifier):
    def __init__(self, client_ids: Sequence[str], *, keys: SigningKeySource | None = None) -> None:
        super().__init__(
            provider=SocialProvider.APPLE,
            client_ids=client_ids,
            issuers=(APPLE_ISSUER,),
            keys=keys or jwt.PyJWKClient(APPLE_JWKS_URL, cache_keys=True, lifespan=3600),
        )

    def verify(self, token: str, nonce: str | None) -> VerifiedSocialAccount:
        claims = self.claims(token)
        if not nonce:
            raise SocialSignInFailed("nonce_missing")
        expected = hashlib.sha256(nonce.encode()).hexdigest()
        if not hmac.compare_digest(str(claims.get("nonce", "")), expected):
            raise SocialSignInFailed("nonce_mismatch")
        # Apple sends the name to the app only, the first time; the app passes it separately.
        return VerifiedSocialAccount(
            provider=SocialProvider.APPLE,
            subject=str(claims["sub"]),
            email=claims.get("email"),
            email_verified=_truthy(claims.get("email_verified")),
        )


class FacebookVerifier:
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        base_url: str = FACEBOOK_GRAPH_URL,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._timeout = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self._app_id and self._app_secret)

    def _get(self, client: httpx.Client, path: str, params: dict[str, str]) -> dict[str, Any]:
        try:
            response = client.get(f"{self._base_url}{path}", params=params)
        except httpx.HTTPError as exc:
            raise SocialSignInFailed("provider_unreachable") from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise SocialSignInFailed("provider_not_json") from exc
        if response.status_code >= 400 or "error" in body:
            raise SocialSignInFailed(f"provider_http_{response.status_code}")
        return body

    def verify(self, token: str) -> VerifiedSocialAccount:
        with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
            debug = self._get(
                client,
                "/debug_token",
                {"input_token": token, "access_token": f"{self._app_id}|{self._app_secret}"},
            ).get("data", {})
            if not debug.get("is_valid"):
                raise SocialSignInFailed("token_invalid")
            if str(debug.get("app_id")) != self._app_id:
                raise SocialSignInFailed("wrong_audience")
            proof = hmac.new(self._app_secret.encode(), token.encode(), hashlib.sha256).hexdigest()
            me = self._get(
                client,
                "/me",
                {"fields": "id,name,email", "access_token": token, "appsecret_proof": proof},
            )
        subject = str(me.get("id") or debug.get("user_id") or "")
        if not subject or subject != str(debug.get("user_id", subject)):
            raise SocialSignInFailed("subject_mismatch")
        return VerifiedSocialAccount(
            provider=SocialProvider.FACEBOOK,
            subject=subject,
            email=me.get("email"),
            # Never trusted to link an existing account; see the module docstring.
            email_verified=False,
            name=me.get("name"),
        )


class ProviderSocialVerifier(SocialVerifier):
    """Routes each provider to its verifier; providers without configuration are off."""

    def __init__(
        self,
        *,
        google: GoogleVerifier | None = None,
        apple: AppleVerifier | None = None,
        facebook: FacebookVerifier | None = None,
    ) -> None:
        self._google = google if google and google.configured else None
        self._apple = apple if apple and apple.configured else None
        self._facebook = facebook if facebook and facebook.configured else None

    def enabled(self) -> set[SocialProvider]:
        return {
            provider
            for provider, verifier in (
                (SocialProvider.GOOGLE, self._google),
                (SocialProvider.APPLE, self._apple),
                (SocialProvider.FACEBOOK, self._facebook),
            )
            if verifier is not None
        }

    def verify(
        self, provider: SocialProvider, token: str, *, nonce: str | None = None
    ) -> VerifiedSocialAccount:
        if not token or len(token) > 8192:
            raise SocialSignInFailed("token_missing")
        if provider is SocialProvider.GOOGLE and self._google:
            return self._google.verify(token)
        if provider is SocialProvider.APPLE and self._apple:
            return self._apple.verify(token, nonce)
        if provider is SocialProvider.FACEBOOK and self._facebook:
            return self._facebook.verify(token)
        raise SocialProviderDisabled(provider.value)
