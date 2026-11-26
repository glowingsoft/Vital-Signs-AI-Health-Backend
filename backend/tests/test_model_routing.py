# Tests for model routing, pricing and the Bedrock caller (app/configurations/, app/shared/bedrock.py,
# app/motors/*/model.py). Nothing here calls AWS: Bedrock is replaced by a botocore Stubber or a fake.

import base64

import pytest
from botocore.stub import Stubber

from app import config
from app.configurations.models import MODELS
from app.configurations.pricing import cost_usd
from app.configurations.routing import ROUTES, model_chain
from app.motors import runner
from app.motors.triage import logic as triage_logic
from app.motors.triage import model as triage_model
from app.motors.visual import model as visual_model
from app.motors.vitals import logic as vitals_logic
from app.motors.vitals import model as vitals_model
from app.shared import bedrock
from tests.test_assess import PATIENT, client, queued_jobs  # noqa: F401  (fixtures)

# Smallest valid PNG header: enough for image_block() to recognise the format.
PNG_BASE64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16).decode()


# ---------- pricing (numbers from docs/AI-Cost-Estimate.docx, section 6) ----------

@pytest.mark.parametrize(
    "model_key,input_tokens,output_tokens,expected",
    [
        ("haiku", 23_430, 1_500, 0.0034023),  # triage conversation
        ("sonnet", 23_430, 1_500, 0.068046),
        ("nova_pro", 23_430, 1_500, 0.023544),
        ("llama", 23_430, 1_500, 0.0179496),
        ("haiku", 2_589, 300, 0.00044979),  # image scan: 1,089 image + 1,500 prompt tokens
        ("sonnet", 2_589, 300, 0.0089958),
        ("nova_pro", 2_800, 300, 0.0032),
        ("haiku", 500, 200, 0.000165),  # vitals explanation
        ("nova_lite", 500, 200, 0.000078),
        ("haiku", 0, 0, 0.0),
    ],
)
def test_cost_usd(model_key, input_tokens, output_tokens, expected):
    assert cost_usd(model_key, input_tokens, output_tokens) == pytest.approx(expected)


def test_only_us_model_ids():
    assert all(model.model_id.startswith("us.") for model in MODELS.values())


# ---------- get_model ----------

@pytest.mark.parametrize("model_module", [triage_model, visual_model, vitals_model])
def test_get_model_default_is_haiku(model_module):
    assert model_module.get_model({}) == "haiku"


def test_get_model_escalation():
    assert triage_model.get_model({}, escalate=True) == "sonnet"
    assert visual_model.get_model({}, escalate=True) == "sonnet"


def test_vitals_never_escalates():
    assert vitals_model.get_model({}, escalate=True) == "haiku"


def test_visual_never_uses_llama():
    route = ROUTES["visual"]
    assert "llama" not in (route.default, route.escalation, *route.fallbacks)
    for escalate in (False, True):
        first = visual_model.get_model({}, escalate)
        assert "llama" not in model_chain("visual", first, has_image=True)


def test_image_skips_text_only_models():
    assert model_chain("triage", "haiku") == ("haiku", "nova_pro", "llama")
    assert model_chain("triage", "haiku", has_image=True) == ("haiku", "nova_pro")


# ---------- shared Bedrock caller (botocore Stubber: the real client, no network) ----------

@pytest.fixture
def stubbed_client():
    client = bedrock.get_client()
    with Stubber(client) as stubber:
        yield stubber


def converse_response(text="ok", input_tokens=10, output_tokens=5):
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": input_tokens, "outputTokens": output_tokens, "totalTokens": input_tokens + output_tokens},
        "metrics": {"latencyMs": 100},
    }


def test_converse_reads_text_and_usage(stubbed_client):
    messages = [{"role": "user", "content": [{"text": "hi"}]}]
    stubbed_client.add_response(
        "converse",
        converse_response("hello", 23_430, 1_500),
        expected_params={
            "modelId": "us.amazon.nova-pro-v1:0",
            "messages": messages,
            "system": [{"text": "be brief"}],
            "inferenceConfig": {"maxTokens": 50},
        },
    )

    result = bedrock.converse("nova_pro", messages, "be brief", 50)

    assert (result.text, result.input_tokens, result.output_tokens) == ("hello", 23_430, 1_500)
    stubbed_client.assert_no_pending_responses()


def test_converse_error_becomes_bedrock_error(stubbed_client):
    stubbed_client.add_client_error("converse", service_error_code="AccessDeniedException", http_status_code=403)

    with pytest.raises(bedrock.BedrockError):
        bedrock.converse("haiku", [{"role": "user", "content": [{"text": "hi"}]}], "", 50)


# ---------- run_model (fake converse) ----------

@pytest.fixture
def use_bedrock(monkeypatch):
    monkeypatch.setattr(config, "USE_BEDROCK", True)


@pytest.fixture
def fake_bedrock(monkeypatch):
    """Replace bedrock.converse. Models in `failing` raise; every call is recorded in `calls`."""

    class Fake:
        failing = set()
        calls = []

    def fake_converse(model_key, messages, system, max_tokens, timeout=None):
        Fake.calls.append(model_key)
        if model_key in Fake.failing:
            raise bedrock.BedrockError(f"{model_key} blocked")
        return bedrock.ConverseResult(
            text='{"risk_level": "medium", "confidence_score": 0.7, "explanation": "Worth reviewing with a healthcare professional."}',
            input_tokens=23_430,
            output_tokens=1_500,
            latency_ms=120,
        )

    Fake.calls = []
    Fake.failing = set()
    monkeypatch.setattr(bedrock, "converse", fake_converse)
    return Fake


def test_run_model_is_stub_when_bedrock_off(monkeypatch, fake_bedrock):
    monkeypatch.setattr(config, "USE_BEDROCK", False)

    result = triage_model.run_model({"message": "cough"})

    assert result.model_id == "stub"
    assert result.cost_usd == 0
    assert fake_bedrock.calls == []  # nothing called AWS


def test_run_model_default_model(use_bedrock, fake_bedrock):
    result = triage_model.run_model({"message": "cough"})

    assert fake_bedrock.calls == ["haiku"]
    assert result.model_key == "haiku"
    assert result.model_id == "us.anthropic.claude-haiku-5-5"
    assert result.fallback_used is False
    assert result.escalated is False
    # cost_usd is filled from the returned token counts
    assert (result.input_tokens, result.output_tokens) == (23_430, 1_500)
    assert result.cost_usd == pytest.approx(0.0034023)


def test_run_model_falls_back_when_default_fails(use_bedrock, fake_bedrock):
    fake_bedrock.failing = {"haiku"}  # e.g. Claude blocked on the AWS account

    result = triage_model.run_model({"message": "cough"})

    assert fake_bedrock.calls == ["haiku", "nova_pro"]
    assert result.model_key == "nova_pro"
    assert result.fallback_used is True
    assert result.cost_usd == pytest.approx(0.023544)


def test_run_model_escalation_falls_back_too(use_bedrock, fake_bedrock):
    fake_bedrock.failing = {"sonnet", "nova_pro"}

    result = triage_model.run_model({"message": "chest pain"}, escalate=True)

    assert fake_bedrock.calls == ["sonnet", "nova_pro", "llama"]
    assert result.model_key == "llama"
    assert (result.escalated, result.fallback_used) == (True, True)


def test_run_model_all_fail_raises(use_bedrock, fake_bedrock):
    fake_bedrock.failing = {"haiku", "nova_pro", "llama"}

    with pytest.raises(runner.AllModelsFailed):
        triage_model.run_model({"message": "cough"})


def test_visual_image_never_reaches_llama(use_bedrock, fake_bedrock):
    fake_bedrock.failing = {"haiku", "nova_pro"}

    with pytest.raises(runner.AllModelsFailed):
        visual_model.run_model({"image": PNG_BASE64})

    assert fake_bedrock.calls == ["haiku", "nova_pro"]


def test_visual_image_counts_one_image(use_bedrock, fake_bedrock):
    result = visual_model.run_model({"image": PNG_BASE64})

    assert result.image_count == 1


def test_visual_without_real_image_makes_no_call(use_bedrock, fake_bedrock):
    result = visual_model.run_model({"image": "img_001"})  # only a reference, nothing to look at

    assert result.model_id == "stub"
    assert fake_bedrock.calls == []


def test_vitals_uses_nova_lite_fallback(use_bedrock, fake_bedrock):
    fake_bedrock.failing = {"haiku"}

    result = vitals_model.run_model({"heart_rate": 72})

    assert fake_bedrock.calls == ["haiku", "nova_lite"]
    assert result.model_key == "nova_lite"


# ---------- analyze(): ModelResult copied into MotorOutput ----------

def test_analyze_copies_model_result_into_motor_output(use_bedrock, fake_bedrock):
    fake_bedrock.failing = {"haiku"}

    output = triage_logic.analyze({"message": "cough"})

    assert output.model_name == "us.amazon.nova-pro-v1:0"
    assert output.model_version == "nova-pro-v1:0"
    assert output.fallback_used is True
    assert (output.input_tokens, output.output_tokens) == (23_430, 1_500)
    assert output.cost_usd == pytest.approx(0.023544)


def test_triage_keyword_rules_are_a_floor(use_bedrock, fake_bedrock):
    # The fake model says "medium"; a red-flag keyword keeps it critical (and escalates).
    output = triage_logic.analyze({"message": "chest pain"})

    assert output.risk_level == "critical"
    assert output.escalated is True
    assert fake_bedrock.calls == ["sonnet"]


def test_vitals_risk_level_never_comes_from_the_model(use_bedrock, fake_bedrock):
    output = vitals_logic.analyze({"heart_rate": 160})

    assert output.risk_level == "critical"  # from the threshold rules
    assert output.model_name == "us.anthropic.claude-haiku-5-5"


def test_stub_output_unchanged_when_bedrock_off(monkeypatch):
    monkeypatch.setattr(config, "USE_BEDROCK", False)

    output = triage_logic.analyze({"message": "headache"})

    assert (output.model_name, output.cost_usd, output.risk_level) == ("stub", 0, "medium")


@pytest.mark.parametrize(
    "text",
    ["not json", '{"risk_level": "severe", "confidence_score": 0.5, "explanation": "x"}',
     '{"risk_level": "low", "confidence_score": 2, "explanation": "x"}', '{"risk_level": "low"}'],
)
def test_parse_answer_rejects_bad_answers(text):
    assert runner.parse_answer(text) is None


def test_parse_answer_accepts_code_fence():
    text = '```json\n{"risk_level": "low", "confidence_score": 0.5, "explanation": "Fine."}\n```'
    assert runner.parse_answer(text) == {
        "risk_level": "low", "confidence_score": 0.5, "explanation": "Fine.", "source_id": None, "finding_type": None,
    }


# ---------- orchestrator: a motor whose models all fail is marked as failed ----------

def test_all_models_failing_marks_motor_failed(client, use_bedrock, fake_bedrock):  # noqa: F811
    fake_bedrock.failing = {"haiku", "nova_pro", "llama", "nova_lite"}
    body = {"symptoms": "cough", "vitals": {"heart_rate": 130, "client_reading_id": "r_9"}, "context": {"country": "US"}}

    data = client.post("/ai/assess", json=body, headers=PATIENT).json()

    assert sorted(data["motors_failed"]) == ["triage", "vitals"]
    assert data["motors_used"] == []
    assert data["risk_level"] == "medium"  # the safe answer when every motor failed
