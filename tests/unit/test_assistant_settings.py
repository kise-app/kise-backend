"""Settings read plain env names — DATABASE_URL, SECRET_KEY, GEMINI_API_KEY — with no prefix."""

from __future__ import annotations

from kise.platform.config import Settings


def test_plain_env_names_are_read(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "s" * 40)
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@host:5432/db")
    monkeypatch.setenv("DEFAULT_CURRENCY", "usd")
    monkeypatch.setenv("GEMINI_API_KEY", "key-from-env")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-other")
    monkeypatch.setenv("ASSISTANT_HOURLY_LIMIT", "5")
    settings = Settings(_env_file=None)
    assert settings.secret_key == "s" * 40
    # A platform's bare postgres:// URL is used directly, with the driver spelled out.
    assert settings.database_url == "postgresql+psycopg://u:p@host:5432/db"
    assert settings.default_currency == "USD"
    assert settings.gemini_api_key == "key-from-env"
    assert settings.gemini_model == "gemini-other"
    assert settings.assistant_hourly_limit == 5
    assert settings.assistant_enabled is True


def test_the_old_prefixed_names_are_not_read(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("KISE_SECRET_KEY", "x" * 40)
    monkeypatch.setenv("KISE_GEMINI_API_KEY", "ignored")
    settings = Settings(_env_file=None)
    assert settings.is_secret_key_insecure is True
    assert settings.gemini_api_key == ""
    assert settings.assistant_enabled is False


def test_settable_in_code_by_field_name():
    settings = Settings(_env_file=None, gemini_api_key="k", assistant_hourly_limit=2)
    assert settings.gemini_api_key == "k"
    assert settings.assistant_hourly_limit == 2
