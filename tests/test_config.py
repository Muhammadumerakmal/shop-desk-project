"""NFR-1: a missing key fails at startup with a sentence; keys never appear in repr."""

import pytest

from shop_desk.config import Settings, StartupError, load_settings


def test_missing_gemini_key_is_one_sentence(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(StartupError) as err:
        load_settings(env_file=None)
    message = str(err.value)
    assert "GEMINI_API_KEY" in message and ".env" in message
    assert "\n" not in message


def test_placeholder_key_counts_as_missing(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "your-gemini-key-here")
    with pytest.raises(StartupError):
        load_settings(env_file=None)


def test_tracing_requires_openai_key_unless_switched_off(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-secret")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("SHOP_DESK_TRACING", "on")
    with pytest.raises(StartupError, match="OPENAI_API_KEY"):
        load_settings(env_file=None)
    monkeypatch.setenv("SHOP_DESK_TRACING", "off")
    assert load_settings(env_file=None).tracing is False


def test_keys_are_not_in_repr():
    s = Settings(fast_model="f", reasoning_model="r", tracing=True, gemini_api_key="g-secret", openai_api_key="o-secret")
    assert "secret" not in repr(s)
