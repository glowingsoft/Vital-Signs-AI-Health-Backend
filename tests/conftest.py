# Shared test setup. A local .env may set USE_BEDROCK=true; tests must never call AWS, so every test
# starts with Bedrock off. Tests that need the model path use the use_bedrock fixture together with
# fake_bedrock (tests/test_model_routing.py), which replaces bedrock.converse.

import pytest

from app import config


@pytest.fixture(autouse=True)
def bedrock_off(monkeypatch):
    monkeypatch.setattr(config, "USE_BEDROCK", False)


@pytest.fixture(autouse=True)
def consent_check_off(monkeypatch):
    # The consent check reads Postgres, which tests do not have. tests/test_consent.py turns it back on
    # with the database replaced by a fake.
    monkeypatch.setattr(config, "CONSENT_CHECK_ENABLED", False)
