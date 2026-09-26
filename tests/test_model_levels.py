"""FR-1: which of the three configuration levels decides the model."""

import os

from agents.testing import assistant_message

from shop_desk.config import DEFAULT_MODEL_ENV

from tests.conftest import FAST


def test_global_default_is_the_fast_model(settings):
    assert os.environ[DEFAULT_MODEL_ENV] == settings.fast_model


def test_desk_declares_no_model(make_session):
    assert make_session().agents.desk.model is None  # inherits the global default


async def test_plain_question_is_answered_by_the_global_default(make_session, provider):
    session = make_session()
    provider.script(FAST, [[assistant_message("Hello! How can I help?")]])
    await session.ask("hi")
    assert provider.requested == [None]  # the Desk asked for "the default"
    assert provider.calls(FAST) == 1


async def test_deleting_global_default_changes_only_the_default_resolution(make_session, provider, monkeypatch):
    session = make_session()
    monkeypatch.setenv(DEFAULT_MODEL_ENV, "sdk-builtin-default")
    provider.script("sdk-builtin-default", [[assistant_message("hi")]])
    await session.ask("hi")
    assert provider.calls("sdk-builtin-default") == 1 and provider.calls(FAST) == 0
