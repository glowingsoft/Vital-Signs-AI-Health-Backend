# Tests for POST /ai/assess with SERVICE=all (orchestrator + all motors in one app).
# Run with: pytest

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config
from app.orchestrator import routes as orchestrator_routes
from main import app
from python_auth.roles import UserType
from python_auth.test_auth import USER_ID, make_token  # NestJS-shaped tokens, signed with the test secret


def auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# A real 1x1 PNG: /ai/assess refuses an image that is not a readable JPEG, PNG, GIF or WEBP.
PNG_IMAGE = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="

# A valid access token for a patient (UserType.User), the only role allowed on /ai/assess.
PATIENT = auth_header(make_token(user_type=UserType.User))


@pytest.fixture
def queued_jobs(monkeypatch):
    """Replace enqueue with a fake that keeps jobs in a list, so tests need no Redis."""
    jobs = []

    async def fake_enqueue(stream, job):
        jobs.append((stream, job))
        return "fake-message-id"

    monkeypatch.setattr(orchestrator_routes, "enqueue", fake_enqueue)
    return jobs


@pytest.fixture
def motor_inputs(monkeypatch):
    """Record every MotorInput the orchestrator sends, then call the motor as usual."""
    sent = {}
    real_call_motor = orchestrator_routes.call_motor

    async def recording_call_motor(client, name, motor_input):
        sent[name] = motor_input
        return await real_call_motor(client, name, motor_input)

    monkeypatch.setattr(orchestrator_routes, "call_motor", recording_call_motor)
    return sent


@pytest.fixture
def client(monkeypatch, queued_jobs):
    # No real server runs during tests, so the orchestrator's HTTP calls to http://localhost:8000
    # would fail. ASGITransport sends them straight into our app in memory instead.
    monkeypatch.setattr(
        orchestrator_routes,
        "make_http_client",
        lambda: httpx.AsyncClient(transport=httpx.ASGITransport(app=app)),
    )
    # TestClient = like Django's test Client: call endpoints without starting a server.
    return TestClient(app)


def test_vitals_critical(client, queued_jobs):
    body = {
        "vitals": {"heart_rate": 160, "spo2": 85, "client_reading_id": "r_1"},
        "context": {"country": "US"},
    }

    data = client.post("/ai/assess", json=body, headers=PATIENT).json()

    assert data["risk_level"] == "critical"
    assert data["motors_used"] == ["vitals"]
    assert data["motors_failed"] == []
    assert data["disclaimer_id"] == "disc_v1"
    assert data["disclaimer"] == "This result is not a diagnosis. Always consult your doctor."
    # One save job was queued, and its assessment id is the prediction_id the app received.
    assert len(queued_jobs) == 1
    stream, job = queued_jobs[0]
    assert stream == "jobs:save_assessment"
    assert str(job.assessment.id) == data["prediction_id"]


def test_symptoms_only(client):
    body = {"symptoms": "headache and dizziness"}

    data = client.post("/ai/assess", json=body, headers=PATIENT).json()

    assert data["risk_level"] == "medium"  # USE_BEDROCK is off in tests: triage answers "ai_unavailable"
    assert data["motors_used"] == ["triage"]
    assert data["motors_failed"] == []


def test_one_motor_failing(client, monkeypatch):
    # Point the triage URL somewhere that returns 404, so the triage call fails.
    monkeypatch.setitem(config.MOTOR_URLS, "triage", "http://localhost:8000/broken")
    body = {
        "symptoms": "headache",
        "vitals": {"heart_rate": 130, "spo2": 96, "client_reading_id": "r_2"},
        "context": {"country": "US"},
    }

    response = client.post("/ai/assess", json=body, headers=PATIENT)
    data = response.json()

    assert response.status_code == 200  # the request still succeeds
    assert data["motors_used"] == ["vitals"]
    assert data["motors_failed"] == ["triage"]
    assert data["risk_level"] == "high"  # heart_rate 130 -> high, from the vitals motor alone


def test_vitals_without_country_is_rejected(client):
    # The database needs a country for every reading, so the API refuses the request up front.
    body = {"vitals": {"heart_rate": 80, "client_reading_id": "r_3"}}

    response = client.post("/ai/assess", json=body, headers=PATIENT)

    assert response.status_code == 422


# ---------- Authentication: the user id always comes from the verified token ----------

SYMPTOMS = {"symptoms": "cough"}


def test_no_token_is_401(client, queued_jobs):
    response = client.post("/ai/assess", json=SYMPTOMS)

    assert response.status_code == 401
    assert queued_jobs == []  # nothing is analysed or saved


@pytest.mark.parametrize(
    "token",
    [
        make_token(secret="some-other-secret-value-xyz"),  # signed with the wrong secret
        make_token(expires_in=-10),  # expired
        make_token(is_refresh=True),  # refresh token used as an access token
        "not-a-jwt",
    ],
    ids=["wrong-secret", "expired", "refresh-token", "garbage"],
)
def test_invalid_token_is_401(client, queued_jobs, token):
    response = client.post("/ai/assess", json=SYMPTOMS, headers=auth_header(token))

    assert response.status_code == 401
    assert queued_jobs == []


@pytest.mark.parametrize("role", [r for r in UserType if r != UserType.User], ids=lambda r: r.name)
def test_non_patient_token_is_403(client, queued_jobs, role):
    response = client.post("/ai/assess", json=SYMPTOMS, headers=auth_header(make_token(user_type=role)))

    assert response.status_code == 403
    assert queued_jobs == []


def test_patient_token_user_id_reaches_motors_and_save_job(client, queued_jobs, motor_inputs):
    body = {
        "symptoms": "headache",
        "image": PNG_IMAGE,
        "vitals": {"heart_rate": 130, "client_reading_id": "r_4"},
        "context": {"country": "US"},
    }

    response = client.post("/ai/assess", json=body, headers=PATIENT)

    assert response.status_code == 200
    # Every motor got the user id from the token.
    assert set(motor_inputs) == {"triage", "visual", "vitals"}
    assert all(motor_input.user_id == USER_ID for motor_input in motor_inputs.values())
    # Every row of the queued save job carries the same id.
    [(_, job)] = queued_jobs
    assert job.reading.user_id == USER_ID
    assert job.assessment.user_id == USER_ID
    assert job.audit_log.actor_id == USER_ID
    assert job.audit_log.patient_id == USER_ID
    assert all(row.user_id == USER_ID for row in job.ai_usage)


def test_user_id_in_body_is_ignored(client, queued_jobs, motor_inputs):
    # An old app version (or an attacker) still sends a user_id: it must have no effect.
    body = {"user_id": "someone_else", "symptoms": "headache"}

    response = client.post("/ai/assess", json=body, headers=PATIENT)

    assert response.status_code == 200
    assert motor_inputs["triage"].user_id == USER_ID
    [(_, job)] = queued_jobs
    assert job.assessment.user_id == USER_ID
    assert job.audit_log.patient_id == USER_ID
    assert "user_id" not in job.assessment.input  # the ignored field is not stored either
