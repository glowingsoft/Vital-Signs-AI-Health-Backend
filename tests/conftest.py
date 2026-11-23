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


@pytest.fixture(autouse=True)
def medical_sources_from_file(monkeypatch):
    # The motors read ai.medical_sources from Postgres. Tests read the seed file db/medical_sources/medical_sources.json
    # instead (same rows), with every status allowed, so the draft list is usable. Only the source lookup is
    # redirected: DB_NAME is left alone, so tests that need the real database (test_worker.py) still reach it.
    # tests/test_medical_sources.py narrows the statuses and replaces read_from_database where it checks them.
    from app.medical_sources import library as medical_sources

    monkeypatch.setattr(medical_sources, "read_from_database", medical_sources.read_from_file)
    monkeypatch.setattr(config, "MEDICAL_SOURCE_STATUSES", ("active", "draft", "proposed_addition"))
    medical_sources.clear_cache()
    yield
    medical_sources.clear_cache()
