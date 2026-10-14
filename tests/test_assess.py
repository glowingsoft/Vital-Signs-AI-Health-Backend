# Tests for POST /ai/assess with SERVICE=all (orchestrator + all motors in one app).
# Run with: pytest

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config
from app.orchestrator import routes as orchestrator_routes
from main import app


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
        "user_id": "u_123",
        "vitals": {"heart_rate": 160, "spo2": 85, "client_reading_id": "r_1"},
        "context": {"country": "US"},
    }

    data = client.post("/ai/assess", json=body).json()

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
    body = {"user_id": "u_123", "symptoms": "headache and dizziness"}

    data = client.post("/ai/assess", json=body).json()

    assert data["risk_level"] == "high"  # "dizziness" is a high-risk keyword in the triage stub
    assert data["motors_used"] == ["triage"]
    assert data["motors_failed"] == []


def test_one_motor_failing(client, monkeypatch):
    # Point the triage URL somewhere that returns 404, so the triage call fails.
    monkeypatch.setitem(config.MOTOR_URLS, "triage", "http://localhost:8000/broken")
    body = {
        "user_id": "u_123",
        "symptoms": "headache",
        "vitals": {"heart_rate": 130, "spo2": 96, "client_reading_id": "r_2"},
        "context": {"country": "US"},
    }

    response = client.post("/ai/assess", json=body)
    data = response.json()

    assert response.status_code == 200  # the request still succeeds
    assert data["motors_used"] == ["vitals"]
    assert data["motors_failed"] == ["triage"]
    assert data["risk_level"] == "high"  # heart_rate 130 -> high, from the vitals motor alone


def test_vitals_without_country_is_rejected(client):
    # The database needs a country for every reading, so the API refuses the request up front.
    body = {"user_id": "u_123", "vitals": {"heart_rate": 80, "client_reading_id": "r_3"}}

    response = client.post("/ai/assess", json=body)

    assert response.status_code == 422
