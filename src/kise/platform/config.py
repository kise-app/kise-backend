"""Configuration, read from the environment by plain names: ``DATABASE_URL``, ``SECRET_KEY`` ...

Each field reads the env var of the same name, upper-cased, with no prefix — so a platform's own
``DATABASE_URL`` (Heroku, Railway, docker-compose) is used as it is, and ``GEMINI_API_KEY`` is the
same name Google's tools use.

Nothing outside ``platform/`` reads settings: adapters are constructed with plain arguments, so a
test can build one without touching the environment.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_DEFAULT_SECRET = "change-me-to-a-long-random-string"


class Settings(BaseSettings):
    """Everything Kise needs to boot."""

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    secret_key: str = INSECURE_DEFAULT_SECRET
    # PostgreSQL is the database Kise runs on. The URL uses the psycopg 3 driver
    # (``postgresql+psycopg``); docker-compose brings up a matching instance. SQLite still works by
    # pointing DATABASE_URL at a ``sqlite://`` URL, which is what the test suite does.
    database_url: str = "postgresql+psycopg://kise:kise@localhost:5432/kise"
    access_token_expire_minutes: int = 60 * 24 * 7  # a week
    default_currency: str = "ETB"
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    debug: bool = False
    # Where the landing page's download buttons point. Each renders only when set; with all three
    # empty (no build published yet) the page shows a "coming soon" note instead of a dead link.
    app_store_url: str = ""
    play_store_url: str = ""
    # A direct link — an APK, a TestFlight invite, a GitHub release.
    app_download_url: str = ""

    # The assistant (voice, receipts, questions). Off until a provider key is set: with no key the
    # assistant routes answer 503 ``assistant_unavailable`` and the app hides the feature.
    assistant_provider: str = "gemini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    # Requests per Owner per rolling hour, per process. 0 disables the limit.
    assistant_hourly_limit: int = 30

    @property
    def assistant_enabled(self) -> bool:
        return self.assistant_provider.lower() == "gemini" and bool(self.gemini_api_key)

    @field_validator("default_currency")
    @classmethod
    def _currency_is_three_letters(cls, value: str) -> str:
        if len(value) != 3 or not value.isalpha():
            raise ValueError("DEFAULT_CURRENCY must be three letters, e.g. ETB")
        return value.upper()

    @field_validator("database_url")
    @classmethod
    def _normalise_database_scheme(cls, value: str) -> str:
        """Rewrite bare Postgres schemes to the psycopg 3 driver Kise runs on.

        Managed providers hand out URLs like ``postgres://...`` or ``postgresql://...``; SQLAlchemy
        needs the driver spelled out as ``postgresql+psycopg://...``. Rewrite only the two bare
        Postgres forms and leave everything else (an already-qualified URL, or ``sqlite://`` used by
        the tests) untouched.
        """
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value[len(prefix) :]
        return value

    @property
    def is_secret_key_insecure(self) -> bool:
        """True when the shipped placeholder is still in use.

        The app factory logs a warning for this rather than refusing to start, so local development
        works out of the box while a deployment has no excuse.
        """
        return self.secret_key == INSECURE_DEFAULT_SECRET or len(self.secret_key) < 32


@lru_cache
def get_settings() -> Settings:
    return Settings()
