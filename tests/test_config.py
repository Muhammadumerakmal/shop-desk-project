"""NFR-1: a missing key fails at startup with a sentence; the key never appears in repr."""

import pytest

from shop_desk.config import (
    DEFAULT_FAST_MODEL,
    DEFAULT_REASONING_MODEL,
    Settings,
    StartupError,
    load_settings,
)


def test_missing_openai_key_is_one_sentence(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(StartupError) as err:
        load_settings(env_file=None)
    message = str(err.value)
    assert "OPENAI_API_KEY" in message and ".env" in message
    assert "\n" not in message


def test_placeholder_key_counts_as_missing(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "your-openai-key-here")
    with pytest.raises(StartupError):
        load_settings(env_file=None)


def test_one_key_serves_model_calls_and_tracing(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret")
    monkeypatch.setenv("SHOP_DESK_TRACING", "on")
    s = load_settings(env_file=None)
    assert s.api_key == "sk-test-secret"
    assert s.tracing is True


def test_tracing_can_be_switched_off_without_a_second_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret")
    monkeypatch.setenv("SHOP_DESK_TRACING", "off")
    assert load_settings(env_file=None).tracing is False


def test_defaults_are_the_cheap_model_then_the_reasoning_one(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret")
    monkeypatch.delenv("FAST_MODEL", raising=False)
    monkeypatch.delenv("REASONING_MODEL", raising=False)
    s = load_settings(env_file=None)
    assert s.fast_model == DEFAULT_FAST_MODEL
    assert s.reasoning_model == DEFAULT_REASONING_MODEL
    assert s.fast_model != s.reasoning_model  # FR-1: the default must be the cheap one


def test_client_points_at_openai_by_default(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret")
    assert load_settings(env_file=None).base_url is None


def test_key_is_not_in_repr():
    s = Settings(fast_model="f", reasoning_model="r", tracing=True, api_key="o-secret")
    assert "secret" not in repr(s)
