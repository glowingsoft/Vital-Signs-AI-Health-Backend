# Tests for the per-motor time limits (app/config.py MOTOR_TIMEOUTS).
# Run with: pytest

import asyncio

import pytest

from app import config
from app.orchestrator import routes
from app.shared.contracts import MotorInput


def test_default_limits():
    assert config.MOTOR_TIMEOUTS == {"triage": 3.0, "visual": 8.0, "vitals": 3.0}


@pytest.mark.parametrize("motor", ["triage", "visual", "vitals"])
def test_motor_stops_before_the_orchestrator_gives_up(motor):
    assert config.motor_budget_seconds(motor) == pytest.approx(
        config.motor_timeout_seconds(motor) - config.MOTOR_REPLY_MARGIN_SECONDS
    )


def test_bedrock_read_timeout_covers_the_slowest_motor():
    assert config.longest_motor_timeout_seconds() == 8.0


@pytest.mark.parametrize("motor", ["triage", "visual", "vitals"])
def test_orchestrator_waits_for_each_motor_with_its_own_limit(motor, monkeypatch):
    waited = []

    async def fake_wait_for(awaitable, timeout):
        waited.append(timeout)
        awaitable.close()  # never actually sent
        raise asyncio.TimeoutError

    monkeypatch.setattr(routes.asyncio, "wait_for", fake_wait_for)
    motor_input = MotorInput(user_id="u_1", timestamp="2026-11-11T10:00:00Z", input_type="text", data={})

    class Client:
        async def post(self, url, json):
            raise AssertionError("not called: wait_for is faked")

    answer, _ = asyncio.run(routes.call_motor(Client(), motor, motor_input))

    assert answer is None  # a motor that is too slow counts as failed
    assert waited == [config.MOTOR_TIMEOUTS[motor]]


def test_gateway_waits_longer_than_the_slowest_motor():
    for conf in ["gateway/nginx.conf", "gateway/nginx.aws.conf"]:
        text = open(conf).read()
        seconds = int(text.split("proxy_read_timeout")[1].split("s;")[0].strip())
        assert seconds > config.longest_motor_timeout_seconds() + 1, conf


def test_http_client_does_not_cut_the_slowest_motor_short():
    # httpx's default (5 s) once made the orchestrator give up on visual (8 s) with a ReadTimeout.
    client = routes.make_http_client()
    assert client.timeout.read > config.longest_motor_timeout_seconds()
    asyncio.run(client.aclose())
