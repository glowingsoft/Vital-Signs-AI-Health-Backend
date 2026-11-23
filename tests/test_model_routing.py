# Tests for model routing, pricing and the Bedrock caller (app/configurations/, app/shared/bedrock.py,
# app/motors/*/model.py). Nothing here calls AWS: Bedrock is replaced by a botocore Stubber or a fake.

import base64
import dataclasses
import json
import time

import pytest
from botocore.stub import Stubber

from app import config
from app.configurations.models import MODELS
from app.configurations.pricing import cost_usd
from app.configurations.routing import (
    ROUTES, TRIAGE_ESCALATION_CONFIDENCE, VISUAL_ESCALATION_CONFIDENCE, VISUAL_MAX_JSON_RETRIES, model_chain,
)
from app.motors import runner
from app.motors.triage import logic as triage_logic
from app.motors.triage import model as triage_model
from app.motors.visual import logic as visual_logic
from app.motors.visual import model as visual_model
from app.motors.vitals import logic as vitals_logic
from app.motors.vitals import model as vitals_model
from app.shared import bedrock, prompts
from app.shared.images import InvalidImage
from tests.test_assess import PATIENT, client, motor_inputs, queued_jobs  # noqa: F401  (fixtures)

# Smallest valid PNG header: enough for image_block() to recognise the format.
PNG_BASE64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16).decode()


# ---------- pricing (numbers from docs/AI-Cost-Estimate.docx, section 6) ----------

@pytest.mark.parametrize(
    "model_key,input_tokens,output_tokens,expected",
    [
        ("haiku", 23_430, 1_500, 0.034023),  # triage conversation
        ("sonnet", 23_430, 1_500, 0.102069),
        ("nova_pro", 23_430, 1_500, 0.023544),
        ("llama", 23_430, 1_500, 0.0179496),
        ("haiku", 2_589, 300, 0.0044979),  # image scan: 1,089 image + 1,500 prompt tokens
        ("sonnet", 2_589, 300, 0.0134937),
        ("nova_pro", 2_800, 300, 0.0032),
        ("haiku", 500, 200, 0.00165),  # vitals explanation
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


MEDIUM_ANSWER = '{"risk_level": "medium", "confidence_score": 0.7, "explanation": "Worth reviewing with a healthcare professional."}'


def answer_json(risk_level, confidence, explanation="Worth reviewing."):
    return json.dumps({"risk_level": risk_level, "confidence_score": confidence, "explanation": explanation})


@pytest.fixture
def fake_bedrock(monkeypatch):
    """Replace bedrock.converse. Models in `failing` raise; `replies[model]` lists the texts that model
    gives, one per call (MEDIUM_ANSWER once they run out). Every call is recorded in `calls` and `messages`."""

    class Fake:
        failing = set()
        replies = {}
        calls = []
        messages = []

    def fake_converse(model_key, messages, system, max_tokens, timeout=None):
        Fake.calls.append(model_key)
        Fake.messages.append(messages)
        if model_key in Fake.failing:
            raise bedrock.BedrockError(f"{model_key} blocked")
        queued = Fake.replies.get(model_key) or []
        return bedrock.ConverseResult(
            text=queued.pop(0) if queued else MEDIUM_ANSWER,
            input_tokens=23_430,
            output_tokens=1_500,
            latency_ms=120,
        )

    Fake.calls = []
    Fake.messages = []
    Fake.failing = set()
    Fake.replies = {}
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
    assert result.model_id == "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert result.fallback_used is False
    assert result.escalated is False
    # cost_usd is filled from the returned token counts
    assert (result.input_tokens, result.output_tokens) == (23_430, 1_500)
    assert result.cost_usd == pytest.approx(0.034023)


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


def test_visual_unreadable_image_makes_no_call(use_bedrock, fake_bedrock):
    with pytest.raises(InvalidImage):
        visual_model.run_model({"image": "img_001"})  # only a reference, nothing to look at

    assert fake_bedrock.calls == []


def test_vitals_uses_nova_lite_fallback(use_bedrock, fake_bedrock):
    fake_bedrock.failing = {"haiku"}

    result = vitals_model.run_model({"heart_rate": 72}, "low")

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


# ---------- triage: escalation, retry on invalid JSON, no AI answer ----------

HAIKU_COST = 0.034023  # 23,430 input + 1,500 output tokens, the fake's usage per call
SONNET_COST = 0.102069
UNAVAILABLE = "We could not complete the check right now. Please contact a healthcare professional."


def test_triage_escalation_threshold():
    assert TRIAGE_ESCALATION_CONFIDENCE == 0.7


def test_triage_confident_low_answer_is_not_escalated(use_bedrock, fake_bedrock):
    fake_bedrock.replies = {"haiku": [answer_json("low", 0.9)]}

    output = triage_logic.analyze({"message": "a small scratch on my hand"})

    assert fake_bedrock.calls == ["haiku"]  # Sonnet is never called
    assert (output.risk_level, output.confidence_score, output.finding_type) == ("low", 0.9, "ai_triage")
    assert output.escalated is False
    assert output.cost_usd == pytest.approx(HAIKU_COST)


def test_triage_high_answer_is_escalated_and_costs_added(use_bedrock, fake_bedrock):
    fake_bedrock.replies = {"haiku": [answer_json("high", 0.9)], "sonnet": [answer_json("critical", 0.95)]}

    output = triage_logic.analyze({"message": "chest pain"})

    assert fake_bedrock.calls == ["haiku", "sonnet"]
    assert (output.risk_level, output.confidence_score) == ("critical", 0.95)  # Sonnet's answer is used
    assert output.escalated is True
    assert output.model_name == "us.anthropic.claude-sonnet-4-6"
    assert (output.input_tokens, output.output_tokens) == (2 * 23_430, 2 * 1_500)
    assert output.cost_usd == pytest.approx(HAIKU_COST + SONNET_COST)


def test_triage_low_confidence_is_escalated(use_bedrock, fake_bedrock):
    fake_bedrock.replies = {"haiku": [answer_json("low", 0.5)], "sonnet": [answer_json("medium", 0.8)]}

    output = triage_logic.analyze({"message": "me duele un poco la cabeza"})

    assert fake_bedrock.calls == ["haiku", "sonnet"]
    assert (output.risk_level, output.escalated) == ("medium", True)


def test_triage_retries_invalid_json_on_the_same_model(use_bedrock, fake_bedrock):
    fake_bedrock.replies = {"haiku": ["Sure! The risk is low.", answer_json("low", 0.9)]}

    result = triage_model.run_model({"message": "cough"})

    assert fake_bedrock.calls == ["haiku", "haiku"]
    assert result.is_retry is True
    assert runner.parse_answer(result.text)["risk_level"] == "low"
    assert (result.input_tokens, result.cost_usd) == (2 * 23_430, pytest.approx(2 * HAIKU_COST))
    # The retry shows the model its bad reply and asks for JSON only.
    retry = fake_bedrock.messages[1]
    assert retry[-2] == {"role": "assistant", "content": [{"text": "Sure! The risk is low."}]}
    assert retry[-1] == {"role": "user", "content": [{"text": runner.RETRY_MESSAGE}]}


def test_triage_uses_the_answer_from_the_retry(use_bedrock, fake_bedrock):
    fake_bedrock.replies = {"haiku": ["not json", answer_json("low", 0.9)]}

    output = triage_logic.analyze({"message": "cough"})

    assert (output.risk_level, output.confidence_score, output.finding_type) == ("low", 0.9, "ai_triage")
    assert output.cost_usd == pytest.approx(2 * HAIKU_COST)


def test_triage_still_invalid_json_moves_to_the_next_model(use_bedrock, fake_bedrock):
    fake_bedrock.replies = {"haiku": ["not json", "still not json"], "nova_pro": [answer_json("low", 0.9)]}

    result = triage_model.run_model({"message": "cough"})

    assert fake_bedrock.calls == ["haiku", "haiku", "nova_pro"]
    assert (result.model_key, result.fallback_used, result.is_retry) == ("nova_pro", True, False)
    assert result.cost_usd == pytest.approx(2 * HAIKU_COST + 0.023544)


def test_triage_all_models_fail_is_medium_not_low(use_bedrock, fake_bedrock):
    fake_bedrock.failing = {"haiku", "nova_pro", "llama"}

    output = triage_logic.analyze({"message": "cough"})

    assert (output.risk_level, output.confidence_score) == ("medium", 0.0)
    assert (output.explanation, output.finding_type) == (UNAVAILABLE, "ai_unavailable")
    assert output.disclaimer_id == "disc_v1"


def test_triage_invalid_json_everywhere_is_unavailable_and_still_costed(use_bedrock, fake_bedrock):
    fake_bedrock.replies = {key: ["nope", "nope"] for key in ("haiku", "nova_pro", "llama")}

    output = triage_logic.analyze({"message": "cough"})

    assert fake_bedrock.calls == ["haiku", "haiku", "nova_pro", "nova_pro", "llama", "llama"]
    assert (output.risk_level, output.finding_type) == ("medium", "ai_unavailable")
    assert output.input_tokens == 6 * 23_430


def test_triage_escalation_never_lowers_the_risk(use_bedrock, fake_bedrock):
    fake_bedrock.replies = {
        "haiku": [answer_json("critical", 0.9, "Haiku says urgent.")],
        "sonnet": [answer_json("low", 0.9, "Sonnet says mild.")],
    }

    output = triage_logic.analyze({"message": "chest pain"})

    assert fake_bedrock.calls == ["haiku", "sonnet"]
    assert (output.risk_level, output.explanation) == ("critical", "Haiku says urgent.")
    assert (output.model_name, output.model_version) == ("us.anthropic.claude-haiku-4-5-20251001-v1:0", "claude-haiku-4-5-20251001")  # the answer shown
    assert output.escalated is True
    assert output.cost_usd == pytest.approx(HAIKU_COST + SONNET_COST)


def test_triage_escalation_can_raise_the_risk(use_bedrock, fake_bedrock):
    fake_bedrock.replies = {
        "haiku": [answer_json("medium", 0.5, "Haiku is unsure.")],
        "sonnet": [answer_json("high", 0.8, "Sonnet says soon.")],
    }

    output = triage_logic.analyze({"message": "headache and dizziness"})

    assert (output.risk_level, output.confidence_score, output.explanation) == ("high", 0.8, "Sonnet says soon.")
    assert output.model_name == "us.anthropic.claude-sonnet-4-6"


def test_triage_same_risk_uses_the_escalation_answer(use_bedrock, fake_bedrock):
    fake_bedrock.replies = {
        "haiku": [answer_json("high", 0.8, "Haiku says soon.")],
        "sonnet": [answer_json("high", 0.95, "Sonnet says soon.")],
    }

    output = triage_logic.analyze({"message": "vomiting"})

    assert (output.risk_level, output.confidence_score, output.explanation) == ("high", 0.95, "Sonnet says soon.")
    assert output.model_name == "us.anthropic.claude-sonnet-4-6"


def test_triage_escalation_failing_keeps_the_first_answer(use_bedrock, fake_bedrock):
    fake_bedrock.replies = {"haiku": [answer_json("high", 0.9, "Haiku says soon.")]}
    fake_bedrock.failing = {"sonnet", "nova_pro", "llama"}

    output = triage_logic.analyze({"message": "chest pain"})

    assert (output.risk_level, output.explanation, output.finding_type) == ("high", "Haiku says soon.", "ai_triage")
    assert output.model_name == "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert output.escalated is True  # the attempt is still counted in the cost reports


def test_triage_invalid_escalation_json_keeps_the_first_answer(use_bedrock, fake_bedrock):
    # Sonnet and its fallbacks only give invalid JSON, even after the retry.
    fake_bedrock.replies = {"haiku": [answer_json("high", 0.9, "Haiku says soon.")]}
    fake_bedrock.replies.update({key: ["nope", "nope"] for key in ("sonnet", "nova_pro", "llama")})

    output = triage_logic.analyze({"message": "chest pain"})

    assert fake_bedrock.calls == ["haiku", "sonnet", "sonnet", "nova_pro", "nova_pro", "llama", "llama"]
    assert (output.risk_level, output.explanation) == ("high", "Haiku says soon.")
    assert output.model_name == "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert output.escalated is True
    assert output.input_tokens == 7 * 23_430  # every call is still costed


def test_triage_unparseable_escalation_result_does_not_crash(use_bedrock, fake_bedrock, monkeypatch):
    # run_chain checks the JSON, but analyze() still guards against an escalation answer it cannot parse.
    real_run_model = triage_logic.run_model

    def run_model(data, escalate=False, deadline=None, country=None):
        result = real_run_model(data, escalate, deadline, country=country)
        return dataclasses.replace(result, text="not json") if escalate else result

    monkeypatch.setattr(triage_logic, "run_model", run_model)
    fake_bedrock.replies = {"haiku": [answer_json("high", 0.9, "Haiku says soon.")]}

    output = triage_logic.analyze({"message": "chest pain"})

    assert (output.risk_level, output.explanation) == ("high", "Haiku says soon.")
    assert output.model_name == "us.anthropic.claude-haiku-4-5-20251001-v1:0"  # Sonnet's cost is added, but its answer is not shown
    assert output.escalated is True
    assert output.cost_usd == pytest.approx(HAIKU_COST + SONNET_COST)


def test_triage_shown_fallback_answer_keeps_its_model(use_bedrock, fake_bedrock):
    fake_bedrock.failing = {"haiku"}
    fake_bedrock.replies = {
        "nova_pro": [answer_json("high", 0.9, "Nova says soon.")],
        "sonnet": [answer_json("low", 0.9, "Sonnet says mild.")],
    }

    output = triage_logic.analyze({"message": "chest pain"})

    assert fake_bedrock.calls == ["haiku", "nova_pro", "sonnet"]
    assert (output.risk_level, output.explanation) == ("high", "Nova says soon.")
    assert (output.model_name, output.fallback_used, output.escalated) == ("us.amazon.nova-pro-v1:0", True, True)
    assert output.cost_usd == pytest.approx(0.023544 + SONNET_COST)


def test_triage_sends_only_the_symptom_text(use_bedrock, fake_bedrock):
    triage_logic.analyze({"message": "cough", "user_id": "u_123", "country": "US"})

    assert fake_bedrock.messages[0] == [{"role": "user", "content": [{"text": "cough"}]}]


def test_triage_shares_one_time_budget(use_bedrock, fake_bedrock, monkeypatch):
    # Haiku's high answer takes the whole budget, so the escalation finds no time left and Sonnet is never called.
    now = [0.0]
    fake_converse = bedrock.converse

    def slow_converse(*args, **kwargs):
        now[0] += config.motor_budget_seconds("triage")
        return fake_converse(*args, **kwargs)

    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    monkeypatch.setattr(bedrock, "converse", slow_converse)
    fake_bedrock.replies = {"haiku": [answer_json("high", 0.9)]}

    output = triage_logic.analyze({"message": "chest pain"})

    assert fake_bedrock.calls == ["haiku"]
    assert (output.risk_level, output.model_name) == ("high", "us.anthropic.claude-haiku-4-5-20251001-v1:0")


def test_triage_prompt_covers_spanish_and_red_flags():
    prompt = prompts.TRIAGE_SYSTEM_PROMPT
    assert "Spanish" in prompt
    for red_flag in ["chest pain", "difficulty breathing", "loss of consciousness", "seizure", "vomiting blood"]:
        assert red_flag in prompt.lower()


def test_vitals_risk_level_never_comes_from_the_model(use_bedrock, fake_bedrock):
    output = vitals_logic.analyze({"heart_rate": 160})

    assert output.risk_level == "critical"  # from the threshold rules
    assert output.model_name == "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def test_triage_without_bedrock_is_unavailable(monkeypatch):
    monkeypatch.setattr(config, "USE_BEDROCK", False)

    output = triage_logic.analyze({"message": "chest pain"})  # no keyword rules any more

    assert (output.model_name, output.cost_usd, output.risk_level) == ("stub", 0, "medium")
    assert (output.confidence_score, output.finding_type) == (0.0, "ai_unavailable")


@pytest.mark.parametrize(
    "text",
    ["not json", '{"risk_level": "severe", "confidence_score": 0.5, "explanation": "x"}',
     '{"risk_level": "low", "confidence_score": 2, "explanation": "x"}', '{"risk_level": "low"}'],
)
def test_parse_answer_rejects_bad_answers(text):
    assert runner.parse_answer(text) is None


def test_parse_answer_accepts_code_fence():
    text = '```json\n{"risk_level": "low", "confidence_score": 0.5, "explanation": "Fine."}\n```'
    assert runner.parse_answer(text) == {"risk_level": "low", "confidence_score": 0.5, "explanation": "Fine.",
        "finding_type": None, "source_id": None}


# ---------- orchestrator: a motor whose models all fail is marked as failed ----------

def test_all_models_failing_still_answers(client, use_bedrock, fake_bedrock):  # noqa: F811
    fake_bedrock.failing = {"haiku", "nova_pro", "llama", "nova_lite"}
    body = {"symptoms": "cough", "vitals": {"heart_rate": 130, "client_reading_id": "r_9"}, "context": {"country": "US"}}

    data = client.post("/ai/assess", json=body, headers=PATIENT).json()

    # Triage answers its "ai_unavailable" result (medium); vitals keeps its rule-based level with the fixed text.
    assert data["motors_failed"] == []
    assert sorted(data["motors_used"]) == ["triage", "vitals"]
    assert data["risk_level"] == "high"  # heart_rate 130, from the vitals rules


# ---------- visual: Haiku first, retries on invalid JSON, Sonnet when Haiku fails or is unsure ----------

# A real 1x1 PNG.
REAL_PNG_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
IMAGE = {"image": REAL_PNG_BASE64}
HAIKU_IMAGE_COST = HAIKU_COST  # the fake reports the same usage for every call
VISUAL_BUSY = (
    "Our AI is busy right now and could not check your image. Please try again in a few minutes, "
    "or ask a doctor to review it."
)
SONNET_ID = "us.anthropic.claude-sonnet-4-6"
HAIKU_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def test_visual_settings():
    assert (VISUAL_ESCALATION_CONFIDENCE, VISUAL_MAX_JSON_RETRIES) == (0.7, 3)
    assert ROUTES["visual"] == ROUTES["visual"].__class__("haiku", "sonnet", ("nova_pro",), 300)


def test_visual_confident_haiku_is_not_checked_again(use_bedrock, fake_bedrock):
    fake_bedrock.replies = {"haiku": [answer_json("low", 0.9)]}

    output = visual_logic.analyze(IMAGE)

    assert fake_bedrock.calls == ["haiku"]
    assert (output.risk_level, output.finding_type, output.model_name) == ("low", "image_description", HAIKU_ID)
    assert (output.escalated, output.fallback_used) == (False, False)
    assert output.image_count == 1


def test_visual_retries_broken_json_on_haiku(use_bedrock, fake_bedrock):
    fake_bedrock.replies = {"haiku": ["broken", "still broken", answer_json("low", 0.9, "Haiku looked.")]}

    output = visual_logic.analyze(IMAGE)

    assert fake_bedrock.calls == ["haiku", "haiku", "haiku"]
    assert (output.risk_level, output.explanation, output.model_name) == ("low", "Haiku looked.", HAIKU_ID)
    assert output.cost_usd == pytest.approx(3 * HAIKU_IMAGE_COST)


def test_visual_retry_is_marked(use_bedrock, fake_bedrock):
    fake_bedrock.replies = {"haiku": ["broken", "still broken", answer_json("low", 0.9)]}

    result = visual_model.run_model(IMAGE, use_fallbacks=False)

    assert result.is_retry is True
    assert (result.input_tokens, result.cost_usd) == (3 * 23_430, pytest.approx(3 * HAIKU_IMAGE_COST))


def test_visual_broken_haiku_after_three_retries_goes_to_sonnet(use_bedrock, fake_bedrock):
    fake_bedrock.replies = {"haiku": ["broken"] * 4, "sonnet": [answer_json("high", 0.8, "Sonnet looked.")]}

    output = visual_logic.analyze(IMAGE)

    assert fake_bedrock.calls == ["haiku"] * 4 + ["sonnet"]
    assert (output.risk_level, output.explanation) == ("high", "Sonnet looked.")
    assert (output.model_name, output.fallback_used) == (SONNET_ID, True)
    assert output.cost_usd == pytest.approx(4 * HAIKU_IMAGE_COST + SONNET_COST)


def test_visual_haiku_timeout_goes_to_sonnet(use_bedrock, fake_bedrock, monkeypatch):
    fake_converse = bedrock.converse

    def converse(model_key, *args, **kwargs):
        if model_key == "haiku":
            fake_bedrock.calls.append(model_key)
            raise bedrock.BedrockError("haiku timed out after 2.50s")
        return fake_converse(model_key, *args, **kwargs)

    monkeypatch.setattr(bedrock, "converse", converse)

    output = visual_logic.analyze(IMAGE)

    assert fake_bedrock.calls == ["haiku", "sonnet"]
    assert (output.model_name, output.fallback_used) == (SONNET_ID, True)


def test_visual_unsure_haiku_sonnet_raises_the_risk(use_bedrock, fake_bedrock):
    fake_bedrock.replies = {
        "haiku": [answer_json("medium", 0.5, "Haiku is unsure.")],
        "sonnet": [answer_json("high", 0.8, "Sonnet looked.")],
    }

    output = visual_logic.analyze(IMAGE)

    assert fake_bedrock.calls == ["haiku", "sonnet"]
    assert (output.risk_level, output.explanation, output.model_name) == ("high", "Sonnet looked.", SONNET_ID)
    assert (output.escalated, output.fallback_used) == (True, False)
    assert output.cost_usd == pytest.approx(HAIKU_IMAGE_COST + SONNET_COST)


def test_visual_unsure_haiku_sonnet_never_lowers_the_risk(use_bedrock, fake_bedrock):
    fake_bedrock.replies = {
        "haiku": [answer_json("high", 0.5, "Haiku says soon.")],
        "sonnet": [answer_json("low", 0.9, "Sonnet says mild.")],
    }

    output = visual_logic.analyze(IMAGE)

    assert (output.risk_level, output.explanation, output.model_name) == ("high", "Haiku says soon.", HAIKU_ID)
    assert output.escalated is True
    assert output.cost_usd == pytest.approx(HAIKU_IMAGE_COST + SONNET_COST)


def test_visual_unsure_haiku_sonnet_failing_keeps_haiku(use_bedrock, fake_bedrock):
    fake_bedrock.replies = {"haiku": [answer_json("medium", 0.5, "Haiku is unsure.")]}
    fake_bedrock.failing = {"sonnet"}

    output = visual_logic.analyze(IMAGE)

    assert fake_bedrock.calls == ["haiku", "sonnet"]  # no Nova Pro to check Haiku again
    assert (output.risk_level, output.explanation, output.model_name) == ("medium", "Haiku is unsure.", HAIKU_ID)
    assert output.escalated is True


def test_visual_all_models_fail_is_busy_answer_with_200(client, use_bedrock, fake_bedrock):  # noqa: F811
    fake_bedrock.failing = {"haiku", "sonnet", "nova_pro"}
    body = {"user_id": "u_1", "timestamp": "2026-10-29T10:00:00Z", "input_type": "image", "data": IMAGE}

    response = client.post("/motor/visual/analyze", json=body)
    output = response.json()

    assert response.status_code == 200
    assert fake_bedrock.calls == ["haiku", "sonnet", "nova_pro"]  # Llama is never called for an image
    assert (output["risk_level"], output["confidence_score"]) == ("medium", 0.0)
    assert (output["finding_type"], output["explanation"]) == ("ai_unavailable", VISUAL_BUSY)


def test_visual_busy_answer_still_records_cost(use_bedrock, fake_bedrock):
    fake_bedrock.replies = {"haiku": ["broken"] * 4}
    fake_bedrock.failing = {"sonnet", "nova_pro"}

    output = visual_logic.analyze(IMAGE)

    assert output.finding_type == "ai_unavailable"
    assert output.cost_usd == pytest.approx(4 * HAIKU_IMAGE_COST)


def test_visual_retries_stop_at_the_deadline(use_bedrock, fake_bedrock, monkeypatch):
    # Each call takes 1 s of the 2.5 s budget: three Haiku calls, then no time for more retries or Sonnet.
    now = [0.0]
    fake_converse = bedrock.converse

    def slow_converse(*args, **kwargs):
        now[0] += 1.0
        return fake_converse(*args, **kwargs)

    monkeypatch.setitem(config.MOTOR_TIMEOUTS, "visual", 2.5)
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    monkeypatch.setattr(bedrock, "converse", slow_converse)
    fake_bedrock.replies = {"haiku": ["broken"] * 4}

    output = visual_logic.analyze(IMAGE)

    assert fake_bedrock.calls == ["haiku"] * 3
    assert output.finding_type == "ai_unavailable"


def test_visual_without_bedrock_is_busy_answer(monkeypatch, fake_bedrock):
    monkeypatch.setattr(config, "USE_BEDROCK", False)

    output = visual_logic.analyze(IMAGE)

    assert fake_bedrock.calls == []
    assert (output.risk_level, output.finding_type, output.cost_usd) == ("medium", "ai_unavailable", 0)
    assert output.explanation == VISUAL_BUSY


def test_visual_without_image_keeps_the_no_image_answer(use_bedrock, fake_bedrock):
    output = visual_logic.analyze({})

    assert output.explanation == "No image was received, so no visual check was made."
    assert fake_bedrock.calls == []


# ---------- visual: backup vs escalation flags ----------

def test_visual_backup_for_failed_haiku_is_not_an_escalation(use_bedrock, fake_bedrock):
    fake_bedrock.failing = {"haiku"}
    fake_bedrock.replies = {"sonnet": [answer_json("medium", 0.9, "Sonnet looked.")]}

    output = visual_logic.analyze(IMAGE)

    assert fake_bedrock.calls == ["haiku", "sonnet"]
    assert (output.escalated, output.fallback_used, output.model_name) == (False, True, SONNET_ID)


def test_visual_unsure_haiku_check_is_an_escalation(use_bedrock, fake_bedrock):
    fake_bedrock.replies = {"haiku": [answer_json("medium", 0.5)], "sonnet": [answer_json("medium", 0.9)]}

    output = visual_logic.analyze(IMAGE)

    assert (output.escalated, output.fallback_used) == (True, False)


def test_visual_nova_pro_after_haiku_and_sonnet_fail_is_a_backup(use_bedrock, fake_bedrock):
    fake_bedrock.failing = {"haiku", "sonnet"}
    fake_bedrock.replies = {"nova_pro": [answer_json("medium", 0.9, "Nova looked.")]}

    output = visual_logic.analyze(IMAGE)

    assert fake_bedrock.calls == ["haiku", "sonnet", "nova_pro"]
    assert (output.escalated, output.fallback_used) == (False, True)
    assert (output.model_name, output.explanation) == ("us.amazon.nova-pro-v1:0", "Nova looked.")


# ---------- images are checked before any motor runs ----------

TOO_BIG_PNG = base64.b64encode(b"\x89PNG" + b"\x00" * config.MAX_IMAGE_BYTES).decode()


@pytest.mark.parametrize(
    "image,detail",
    [
        ("hello", "image is not valid base64"),
        (base64.b64encode(b"abc").decode(), "image must be JPEG, PNG, GIF or WEBP"),
        (TOO_BIG_PNG, "image is larger than 3.75 MB"),
    ],
)
def test_assess_rejects_bad_image(client, motor_inputs, queued_jobs, image, detail):  # noqa: F811
    response = client.post("/ai/assess", json={"symptoms": "cough", "image": image}, headers=PATIENT)

    assert response.status_code == 422
    assert detail in response.text
    assert motor_inputs == {}  # no motor was called
    assert queued_jobs == []


def test_visual_endpoint_rejects_bad_image(client, use_bedrock, fake_bedrock):  # noqa: F811
    body = {"user_id": "u_1", "timestamp": "2026-10-29T10:00:00Z", "input_type": "image", "data": {"image": "hello"}}

    response = client.post("/motor/visual/analyze", json=body)

    assert response.status_code == 422
    assert "image is not valid base64" in response.text
    assert fake_bedrock.calls == []


# ---------- visual retries: the photo is only sent again when needed ----------

def has_image(messages):
    return any("image" in block for message in messages for block in message["content"])


def test_visual_retry_with_a_reply_leaves_the_image_out(use_bedrock, fake_bedrock):
    fake_bedrock.replies = {"haiku": ["Risk: low", "Still: low", answer_json("low", 0.9)]}

    result = visual_model.run_model(IMAGE, use_fallbacks=False)

    assert [has_image(messages) for messages in fake_bedrock.messages] == [True, False, False]
    retry = fake_bedrock.messages[1]
    assert retry[0] == {"role": "user", "content": [{"text": "Describe this image."}]}
    assert retry[1] == {"role": "assistant", "content": [{"text": "Risk: low"}]}
    assert retry[2]["content"][0]["text"] == (
        runner.RETRY_MESSAGE + " Use only your previous reply; do not describe anything new."
    )
    assert fake_bedrock.messages[2][1] == {"role": "assistant", "content": [{"text": "Still: low"}]}
    assert result.image_count == 1  # only the first call sent the image


def test_visual_retry_after_an_empty_reply_sends_the_image_again(use_bedrock, fake_bedrock):
    fake_bedrock.replies = {"haiku": ["", answer_json("low", 0.9)]}

    result = visual_model.run_model(IMAGE, use_fallbacks=False)

    assert [has_image(messages) for messages in fake_bedrock.messages] == [True, True]
    assert result.image_count == 2


def test_visual_image_count_adds_up_across_haiku_and_sonnet(use_bedrock, fake_bedrock):
    fake_bedrock.replies = {"haiku": [answer_json("medium", 0.5)], "sonnet": [answer_json("medium", 0.9)]}

    output = visual_logic.analyze(IMAGE)

    assert output.image_count == 2


def test_triage_retry_is_unchanged(use_bedrock, fake_bedrock):
    fake_bedrock.replies = {"haiku": ["not json", answer_json("low", 0.9)]}

    triage_model.run_model({"message": "cough"})

    assert fake_bedrock.messages[1][-1] == {"role": "user", "content": [{"text": runner.RETRY_MESSAGE}]}


# ---------- latency is the wall-clock time of every attempt ----------

def test_visual_latency_includes_the_timed_out_haiku(use_bedrock, fake_bedrock, monkeypatch):
    now = [0.0]
    fake_converse = bedrock.converse

    def converse(model_key, *args, **kwargs):
        if model_key == "haiku":
            now[0] += 2.0
            fake_bedrock.calls.append(model_key)
            raise bedrock.BedrockError("haiku timed out after 2.00s")
        now[0] += 0.3
        return fake_converse(model_key, *args, **kwargs)

    monkeypatch.setitem(config.MOTOR_TIMEOUTS, "visual", 2.5)
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    monkeypatch.setattr(bedrock, "converse", converse)

    shown = []  # MotorOutput has no latency field: capture the ModelResult analyze() reports
    monkeypatch.setattr(visual_logic, "usage_fields", lambda result: shown.append(result) or runner.usage_fields(result))

    output = visual_logic.analyze(IMAGE)

    assert fake_bedrock.calls == ["haiku", "sonnet"]
    assert output.model_name == SONNET_ID
    assert shown[0].latency_ms == 2300  # 2.0 s timed-out Haiku + 0.3 s Sonnet


# ---------- vitals: rule-based risk level, AI explanation only, HRV and sleep saved ----------

def test_vitals_all_models_fail_keeps_the_rule_answer(use_bedrock, fake_bedrock, monkeypatch):
    fake_bedrock.failing = {"haiku", "nova_lite"}

    output = vitals_logic.analyze({"spo2": 85})

    assert fake_bedrock.calls == ["haiku", "nova_lite"]
    assert (output.risk_level, output.explanation) == ("critical", vitals_logic.EXPLANATIONS["critical"])
    assert (output.model_name, output.cost_usd) == ("stub", 0)  # failed calls cost nothing


def test_vitals_failed_chain_cost_is_still_recorded(use_bedrock, monkeypatch):
    def run_model(data, risk_level, country=None):
        raise runner.AllModelsFailed("vitals: every model failed", 500, 200, 0.00165, 900)

    monkeypatch.setattr(vitals_logic, "run_model", run_model)

    output = vitals_logic.analyze({"spo2": 85})

    assert output.risk_level == "critical"
    assert (output.input_tokens, output.output_tokens, output.cost_usd) == (500, 200, pytest.approx(0.00165))


def test_vitals_endpoint_answers_200_when_every_model_fails(client, use_bedrock, fake_bedrock):  # noqa: F811
    fake_bedrock.failing = {"haiku", "nova_lite"}
    body = {"user_id": "u_1", "timestamp": "2026-10-29T10:00:00Z", "input_type": "vitals", "data": {"spo2": 85}}

    response = client.post("/motor/vitals/analyze", json=body)

    assert response.status_code == 200
    assert response.json()["risk_level"] == "critical"


def test_assess_vitals_used_when_every_model_fails(client, use_bedrock, fake_bedrock):  # noqa: F811
    fake_bedrock.failing = {"haiku", "nova_lite"}
    body = {"vitals": {"spo2": 85, "client_reading_id": "r_10"}, "context": {"country": "US"}}

    data = client.post("/ai/assess", json=body, headers=PATIENT).json()

    assert (data["motors_used"], data["motors_failed"], data["risk_level"]) == (["vitals"], [], "critical")


def test_vitals_model_gets_the_risk_level_and_readings_only(client, use_bedrock, fake_bedrock):  # noqa: F811
    body = {
        "vitals": {"heart_rate": 125, "spo2": 93, "hrv_ms": 40, "sleep_minutes": 300, "client_reading_id": "r_11"},
        "context": {"country": "US"},
    }

    client.post("/ai/assess", json=body, headers=PATIENT)

    assert fake_bedrock.messages == [[{"role": "user", "content": [{"text": (
        "Risk level (already decided, do not change it): high. Readings: heart_rate=125, spo2=93"
    )}]}]]  # no user id, reading id, country, HRV or sleep


def test_vitals_prompt_says_the_level_is_decided():
    prompt = prompts.VITALS_SYSTEM_PROMPT
    assert "already been decided" in prompt
    assert "never state a different risk level" in prompt
    assert "Never call the readings normal or fine" in prompt


@pytest.mark.parametrize(
    "readings,expected",
    [
        ({"systolic": 185}, "critical"),
        ({"diastolic": 95}, "high"),
        ({"systolic": 120}, "low"),
        ({"systolic": 125, "diastolic": 125}, "critical"),
        ({"systolic": 135, "diastolic": 70}, "medium"),
    ],
)
def test_vitals_blood_pressure_numbers_are_checked_on_their_own(readings, expected):
    assert vitals_logic.analyze(readings).risk_level == expected


@pytest.mark.parametrize(
    "check,value,expected",
    [
        (vitals_logic.check_systolic, 180, "critical"), (vitals_logic.check_systolic, 140, "high"),
        (vitals_logic.check_systolic, 130, "medium"), (vitals_logic.check_systolic, 129, "low"),
        (vitals_logic.check_diastolic, 120, "critical"), (vitals_logic.check_diastolic, 90, "high"),
        (vitals_logic.check_diastolic, 80, "medium"), (vitals_logic.check_diastolic, 79, "low"),
    ],
)
def test_vitals_blood_pressure_limits(check, value, expected):
    assert check(value) == expected


def test_assess_saves_hrv_and_sleep_only(client, queued_jobs, use_bedrock, fake_bedrock):  # noqa: F811
    body = {"vitals": {"hrv_ms": 45.5, "sleep_minutes": 420, "client_reading_id": "x"}, "context": {"country": "US"}}

    response = client.post("/ai/assess", json=body, headers=PATIENT)
    data = response.json()

    assert response.status_code == 200
    assert data["risk_level"] == "low"
    assert fake_bedrock.calls == []  # no AI call, no cost
    _, job = queued_jobs[0]
    assert (job.reading.hrv_ms, job.reading.sleep_minutes) == (45.5, 420)
    vitals_row = next(row for row in job.motor_results if row.motor == "vitals")
    assert vitals_row.finding_type == "data_saved"


def test_vitals_hrv_and_sleep_only_answer():
    output = vitals_logic.analyze({"hrv_ms": 45.5})

    assert (output.risk_level, output.confidence_score, output.finding_type) == ("low", 0.8, "data_saved")
    assert output.explanation == vitals_logic.DATA_SAVED_EXPLANATION
    assert output.cost_usd == 0


@pytest.mark.parametrize("reading", [{"hrv_ms": -1}, {"sleep_minutes": 2000}, {"hrv_ms": 100000}])
def test_assess_rejects_bad_hrv_or_sleep(client, reading):  # noqa: F811
    body = {"vitals": {**reading, "client_reading_id": "x"}, "context": {"country": "US"}}

    assert client.post("/ai/assess", json=body, headers=PATIENT).status_code == 422


def test_hrv_is_saved_but_not_used_or_sent(client, queued_jobs, use_bedrock, fake_bedrock):  # noqa: F811
    body = {"vitals": {"heart_rate": 130, "hrv_ms": 40, "client_reading_id": "r_12"}, "context": {"country": "US"}}

    data = client.post("/ai/assess", json=body, headers=PATIENT).json()

    assert data["risk_level"] == "high"
    _, job = queued_jobs[0]
    assert job.reading.hrv_ms == 40
    sent = fake_bedrock.messages[0][0]["content"][0]["text"]
    assert "hrv" not in sent and "Readings: heart_rate=130" in sent
