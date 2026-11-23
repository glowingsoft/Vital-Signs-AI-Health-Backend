# Tests for the consent check of POST /ai/assess (app/orchestrator/consent_check.py).
# No real database: the session factory is replaced by a fake that holds ai.consents rows in a list.
# Run with: pytest

from collections import namedtuple
from datetime import datetime, timedelta, timezone

import pytest

from app import config
from app.configurations.consent import CONSENT_MESSAGES, CONSENT_REQUIRED_MESSAGE
from app.orchestrator import consent_check
from python_auth.test_auth import USER_ID
from tests.test_assess import PATIENT, PNG_IMAGE, client, motor_inputs, queued_jobs  # noqa: F401  (fixtures)

Row = namedtuple("Row", "user_id consent_type granted created_at")

NOW = datetime.now(timezone.utc)


def granted(consent_type, user_id=USER_ID, minutes_ago=10):
    return Row(user_id, consent_type, True, NOW - timedelta(minutes=minutes_ago))


def withdrawn(consent_type, user_id=USER_ID, minutes_ago=1):
    return Row(user_id, consent_type, False, NOW - timedelta(minutes=minutes_ago))


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeSession:
    """Answers NEWEST_CONSENTS_SQL the way Postgres would: the newest row per consent_type."""

    def __init__(self, table, queries):
        self.table = table
        self.queries = queries

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, statement, params):
        self.queries.append((str(statement), params))
        newest = {}
        for row in sorted(self.table, key=lambda row: row.created_at, reverse=True):
            if row.user_id == params["user_id"] and row.consent_type in params["types"]:
                newest.setdefault(row.consent_type, row)
        return FakeResult(list(newest.values()))


@pytest.fixture
def consents(monkeypatch):
    """Turn the consent check on, with ai.consents replaced by this list. Each query is kept in .queries."""

    class Table(list):
        queries = []

    table = Table()
    monkeypatch.setattr(config, "CONSENT_CHECK_ENABLED", True)
    monkeypatch.setattr(consent_check, "get_session_factory", lambda: lambda: FakeSession(table, table.queries))
    return table


@pytest.fixture
def database_down(monkeypatch):
    class DownSession(FakeSession):
        async def execute(self, statement, params):
            raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(config, "CONSENT_CHECK_ENABLED", True)
    monkeypatch.setattr(consent_check, "get_session_factory", lambda: lambda: DownSession([], []))


SYMPTOMS = {"symptoms": "headache"}
IMAGE = {"image": PNG_IMAGE}


def test_symptoms_with_monitoring_and_triage_granted_is_200(client, consents):
    consents.append(granted("monitoring_fda"))
    consents.append(granted("triage"))

    response = client.post("/ai/assess", json=SYMPTOMS, headers=PATIENT)

    assert response.status_code == 200


def test_one_query_for_the_needed_types(client, consents):
    consents.append(granted("monitoring_fda"))
    consents.append(granted("triage"))
    consents.append(granted("image_processing"))

    client.post("/ai/assess", json={**SYMPTOMS, **IMAGE}, headers=PATIENT)

    [(sql, params)] = consents.queries
    assert "DISTINCT ON (consent_type)" in sql
    assert "ORDER BY consent_type, created_at DESC" in sql
    assert params == {"user_id": USER_ID, "types": ["monitoring_fda", "triage", "image_processing"]}


def test_image_without_image_processing_consent_is_403(client, consents, motor_inputs, queued_jobs):
    consents.append(granted("monitoring_fda"))

    response = client.post("/ai/assess", json=IMAGE, headers=PATIENT)

    assert response.status_code == 403
    assert response.json() == {
        "code": "consent_required",
        "message": CONSENT_REQUIRED_MESSAGE,
        "missing": [{"type": "image_processing", "message": CONSENT_MESSAGES["image_processing"]}],
    }
    assert motor_inputs == {}  # no motor was called
    assert queued_jobs == []


def test_withdrawn_consent_is_403_even_if_granted_before(client, consents, motor_inputs):
    consents.append(granted("triage"))
    consents.append(granted("monitoring_fda", minutes_ago=60))
    consents.append(withdrawn("monitoring_fda", minutes_ago=1))

    response = client.post("/ai/assess", json=SYMPTOMS, headers=PATIENT)

    assert response.status_code == 403
    assert [item["type"] for item in response.json()["missing"]] == ["monitoring_fda"]
    assert motor_inputs == {}


def test_granted_again_after_withdrawal_is_200(client, consents):
    consents.append(granted("triage"))
    consents.append(withdrawn("monitoring_fda", minutes_ago=60))
    consents.append(granted("monitoring_fda", minutes_ago=1))

    assert client.post("/ai/assess", json=SYMPTOMS, headers=PATIENT).status_code == 200


def test_no_rows_is_403_with_monitoring_and_triage(client, consents):
    response = client.post("/ai/assess", json=SYMPTOMS, headers=PATIENT)

    assert response.status_code == 403
    assert response.json() == {
        "code": "consent_required",
        "message": CONSENT_REQUIRED_MESSAGE,
        "missing": [
            {"type": "monitoring_fda", "message": CONSENT_MESSAGES["monitoring_fda"]},
            {"type": "triage", "message": CONSENT_MESSAGES["triage"]},
        ],
    }


def test_database_down_is_503(client, database_down, motor_inputs):
    response = client.post("/ai/assess", json=SYMPTOMS, headers=PATIENT)

    assert response.status_code == 503
    body = response.json()
    assert (body["code"], body["missing"]) == ("consent_check_unavailable", [])
    assert body["message"]
    assert motor_inputs == {}


def test_check_disabled_does_not_ask_the_database(client, consents, monkeypatch):
    monkeypatch.setattr(config, "CONSENT_CHECK_ENABLED", False)

    response = client.post("/ai/assess", json=SYMPTOMS, headers=PATIENT)

    assert response.status_code == 200  # no consent rows at all, but the check is off
    assert consents.queries == []


def test_user_id_in_body_is_ignored(client, consents):
    # Another user has consented; the caller (the token's user) has not.
    consents.append(granted("monitoring_fda", user_id="someone_else"))

    response = client.post("/ai/assess", json={"user_id": "someone_else", **SYMPTOMS}, headers=PATIENT)

    assert response.status_code == 403
    [(_, params)] = consents.queries
    assert params["user_id"] == USER_ID


def test_needed_consents_are_combined_without_duplicates():
    from app.shared.contracts import AssessRequest

    request = AssessRequest(
        symptoms="cough", image=PNG_IMAGE,
        vitals={"heart_rate": 80, "client_reading_id": "r"}, context={"country": "US"},
    )

    assert consent_check.needed_consents(request) == ["monitoring_fda", "triage", "image_processing"]


def test_voice_recording_consent_is_needed_only_with_audio():
    from app.shared.contracts import AssessRequest

    spoken = AssessRequest(symptoms="cough", audio_key="1794548444899-a1b2c3.m4a")
    typed = AssessRequest(symptoms="cough")

    assert consent_check.needed_consents(spoken) == ["monitoring_fda", "triage", "voice_recording"]
    assert consent_check.needed_consents(typed) == ["monitoring_fda", "triage"]
