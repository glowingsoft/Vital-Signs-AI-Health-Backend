# Tests for the source each model gives with its answer: finding_type and a source_id from the trusted library.
# The backend checks the id and attaches the citation (app/medical_sources/library.py); saved to ai.motor_results.
# The app gets only the patient label and the library link, in POST /ai/assess "sources". The library itself is tested in tests/test_medical_sources.py.
# Run with: pytest

import json

import pytest

from app.medical_sources import library
from app.motors import runner
from app.motors.triage import logic as triage_logic
from app.motors.visual import logic as visual_logic
from app.motors.vitals import logic as vitals_logic
from app.orchestrator import routes as orchestrator_routes
from app.shared import prompts
from tests.test_assess import PATIENT, client, motor_inputs, queued_jobs  # noqa: F401  (fixtures)
from tests.test_model_routing import PNG_BASE64, fake_bedrock, use_bedrock  # noqa: F401  (fixtures)

CHEST_PAIN_US = "US-AHA-CHESTPAIN-2021"
CHEST_PAIN_LINK = "https://doi.org/10.1161/CIR.0000000000001029"


def answer_with_source(risk_level="medium", **source):
    return json.dumps({
        "risk_level": risk_level, "confidence_score": 0.9,
        "explanation": "Worth reviewing with a healthcare professional.", **source,
    })


@pytest.mark.parametrize(
    "prompt", [prompts.TRIAGE_SYSTEM_PROMPT, prompts.VISUAL_SYSTEM_PROMPT, prompts.VITALS_SYSTEM_PROMPT]
)
def test_every_prompt_asks_for_a_source_id_and_forbids_made_up_ones(prompt):
    for field in ["finding_type", "source_id"]:
        assert f'"{field}"' in prompt
    assert '"reference"' not in prompt  # the model never writes a reference itself
    for rule in ["1. ", "2. ", "3. ", "4. "]:
        assert rule in prompt
    assert "Never make up a source or a link" in prompt
    assert "pending medical review" in prompt


def test_parse_answer_keeps_the_source_id():
    answer = runner.parse_answer(answer_with_source(finding_type="chest_pain", source_id=CHEST_PAIN_US))
    assert (answer["finding_type"], answer["source_id"]) == ("chest_pain", CHEST_PAIN_US)


def test_source_is_optional_and_non_text_or_blank_values_are_dropped():
    answer = runner.parse_answer(answer_with_source(finding_type=["x"], source_id="  "))
    assert answer is not None  # still a valid answer: no retry for a missing source
    assert (answer["finding_type"], answer["source_id"]) == (None, None)


def test_long_source_id_is_cut():
    answer = runner.parse_answer(answer_with_source(source_id="x" * 2000))
    assert len(answer["source_id"]) == runner.MAX_SOURCE_CHARS


def test_triage_prompt_lists_only_the_patients_country(use_bedrock, fake_bedrock, monkeypatch):
    systems = []
    real_converse = runner.bedrock.converse

    def recording_converse(model_key, messages, system, max_tokens, timeout=None):
        systems.append(system)
        return real_converse(model_key, messages, system, max_tokens, timeout=timeout)

    monkeypatch.setattr(runner.bedrock, "converse", recording_converse)
    fake_bedrock.replies = {"haiku": [answer_with_source("low", source_id="none")]}

    triage_logic.analyze({"message": "mild headache"}, country="MX")

    assert "MX-CENETEC-SCASEST-2018" in systems[0]
    assert "US-AHA" not in systems[0]


def test_triage_matched_source_gets_the_library_citation(use_bedrock, fake_bedrock):
    fake_bedrock.replies = {"haiku": [answer_with_source("low", finding_type="chest_pain", source_id=CHEST_PAIN_US)]}

    output = triage_logic.analyze({"message": "mild chest discomfort"}, country="US")

    assert (output.source_id, output.source_status, output.evidence_level) == (
        CHEST_PAIN_US, "matched", "clinical_guideline",
    )
    assert output.finding_type == "chest_pain"
    assert "Guideline for the Evaluation and Diagnosis of Chest Pain" in output.reference  # full doctor citation
    assert "Circulation. 2021" in output.reference
    assert CHEST_PAIN_LINK in output.reference
    assert output.source_link == CHEST_PAIN_LINK
    assert output.source_label == "according to American Heart Association guidelines"


def test_triage_without_source_is_pending_medical_review(use_bedrock, fake_bedrock):
    fake_bedrock.replies = {"haiku": [answer_with_source("low")]}

    output = triage_logic.analyze({"message": "tired"}, country="US")

    assert (output.finding_type, output.source_id, output.reference) == ("ai_triage", None, None)
    assert output.source_status == output.evidence_level == "pending_medical_review"


@pytest.mark.parametrize("source_id", [
    "AHA-2017-made-up",  # not in the library
    "MX-CENETEC-SCASEST-2018",  # in the library, but for Mexico, and the patient is in the US
    "https://www.heart.org/some-guideline",  # a link written by the model
])
def test_triage_source_not_in_the_list_is_ignored(use_bedrock, fake_bedrock, source_id):
    fake_bedrock.replies = {"haiku": [answer_with_source("low", source_id=source_id)]}

    output = triage_logic.analyze({"message": "chest discomfort"}, country="US")

    assert (output.source_id, output.reference, output.source_link) == (None, None, None)
    assert output.source_status == "pending_medical_review"


def test_unknown_country_has_no_sources(use_bedrock, fake_bedrock):
    fake_bedrock.replies = {"haiku": [answer_with_source("low", source_id=CHEST_PAIN_US)]}

    output = triage_logic.analyze({"message": "chest discomfort"}, country="Canada")

    assert output.source_status == "pending_medical_review"


def test_triage_escalated_answer_gives_its_own_source(use_bedrock, fake_bedrock):
    fake_bedrock.replies = {
        "haiku": [answer_with_source("high", finding_type="chest_discomfort", source_id="none")],
        "sonnet": [answer_with_source("critical", finding_type="chest_pain", source_id=CHEST_PAIN_US)],
    }

    output = triage_logic.analyze({"message": "chest pain"}, country="US")

    assert (output.risk_level, output.finding_type, output.source_id) == ("critical", "chest_pain", CHEST_PAIN_US)


def test_visual_source_is_checked_for_the_visual_motor(use_bedrock, fake_bedrock):
    # MX-CENETEC-UPP-2015 (pressure ulcers) is a visual source for Mexico.
    fake_bedrock.replies = {"haiku": [answer_with_source("medium", finding_type="ulcer", source_id="MX-CENETEC-UPP-2015")]}

    output = visual_logic.analyze({"image": PNG_BASE64}, country="Mexico")

    assert (output.finding_type, output.source_id, output.source_status) == ("ulcer", "MX-CENETEC-UPP-2015", "matched")


def test_source_of_another_motor_is_ignored(use_bedrock, fake_bedrock):
    # US-CDC-STROKE is only a triage source.
    fake_bedrock.replies = {"haiku": [answer_with_source("medium", source_id="US-CDC-STROKE")]}

    output = visual_logic.analyze({"image": PNG_BASE64}, country="US")

    assert output.source_status == "pending_medical_review"


def test_vitals_matched_source(use_bedrock, fake_bedrock):
    fake_bedrock.replies = {"haiku": [json.dumps({
        "explanation": "Your oxygen reading appears low. Please contact a healthcare professional right away.",
        "finding_type": "low_oxygen", "source_id": "US-WHO-BEC-2018",
    })]}

    output = vitals_logic.analyze({"spo2": 85}, country="US")

    assert output.risk_level == "critical"  # still from the rules
    assert output.explanation.startswith("Your oxygen reading appears low.")
    assert (output.finding_type, output.source_id, output.source_status) == ("low_oxygen", "US-WHO-BEC-2018", "matched")
    assert output.source_label == "according to World Health Organization guidelines"


def test_vitals_plain_text_reply_is_the_explanation_without_source(use_bedrock, fake_bedrock):
    fake_bedrock.replies = {"haiku": ["Your readings appear slightly high. Talk to a healthcare professional."]}

    output = vitals_logic.analyze({"heart_rate": 110}, country="US")

    assert output.explanation == "Your readings appear slightly high. Talk to a healthcare professional."
    assert (output.finding_type, output.reference, output.source_status) == (
        "vital_thresholds", None, "pending_medical_review",
    )


def test_vitals_without_bedrock_uses_the_fixed_text_and_no_source():
    output = vitals_logic.analyze({"spo2": 85}, country="US")

    assert output.explanation == vitals_logic.EXPLANATIONS["critical"]
    assert (output.finding_type, output.reference, output.source_status) == (
        "vital_thresholds", None, "pending_medical_review",
    )


def test_orchestrator_sends_the_country_and_saves_the_source(client, motor_inputs, queued_jobs, use_bedrock, fake_bedrock):  # noqa: F811
    fake_bedrock.replies = {"haiku": [answer_with_source("low", finding_type="chest_pain", source_id=CHEST_PAIN_US)]}

    response = client.post(
        "/ai/assess", json={"symptoms": "mild chest discomfort", "context": {"country": "US"}}, headers=PATIENT,
    )

    assert response.status_code == 200
    assert motor_inputs["triage"].country == "US"
    (_, job), = queued_jobs
    (row,) = job.motor_results
    assert (row.source_id, row.source_status) == (CHEST_PAIN_US, "matched")
    assert CHEST_PAIN_LINK in row.reference


def library_source(source_id):
    return next(source for source in library.load_sources() if source["id"] == source_id)


def assess(client, body):  # noqa: F811
    response = client.post("/ai/assess", json=body, headers=PATIENT)
    assert response.status_code == 200
    return response.json()


def test_matched_source_is_sent_to_the_app_as_label_and_link(client, use_bedrock, fake_bedrock):  # noqa: F811
    fake_bedrock.replies = {"haiku": [answer_with_source("low", finding_type="chest_pain", source_id=CHEST_PAIN_US)]}

    data = assess(client, {"symptoms": "mild chest discomfort", "context": {"country": "US"}})

    source = library_source(CHEST_PAIN_US)
    assert data["sources"] == [
        {"motor": "triage", "status": "matched", "label": source["patient_label"], "link": source["link"]},
    ]
    assert data["sources"][0]["label"] == "according to American Heart Association guidelines"
    assert data["sources"][0]["link"] == CHEST_PAIN_LINK


@pytest.mark.parametrize("country, label", [
    ("US", "Pending medical review"),
    ("United States", "Pending medical review"),
    ("Canada", "Pending medical review"),  # unknown country -> English
    ("MX", "Pendiente de revisión médica"),
    ("México", "Pendiente de revisión médica"),
])
def test_no_match_is_pending_medical_review_in_the_patients_language(client, use_bedrock, fake_bedrock, country, label):  # noqa: F811
    fake_bedrock.replies = {"haiku": [answer_with_source("low", source_id="none")]}

    data = assess(client, {"symptoms": "tired", "context": {"country": country}})

    assert data["sources"] == [{"motor": "triage", "status": "pending_medical_review", "label": label, "link": None}]


def test_no_context_is_pending_medical_review_in_english(client):  # noqa: F811
    data = assess(client, {"symptoms": "tired"})

    assert data["sources"][0]["label"] == "Pending medical review"


def test_answer_without_source_fields_gets_the_default_label(client):  # noqa: F811
    # Without Bedrock the vitals motor answers from its fixed texts and the triage stub has no source_status:
    # nothing was assessed, so both get the default message.
    data = assess(client, {
        "symptoms": "tired",
        "vitals": {"heart_rate": 72, "client_reading_id": "r_src"},
        "context": {"country": "MX"},
    })

    assert [source["label"] for source in data["sources"]] == ["Pendiente de revisión médica"] * 2
    assert all(source["link"] is None for source in data["sources"])


def test_two_motors_give_two_sources_in_motors_used_order(client, use_bedrock, fake_bedrock):  # noqa: F811
    # The same reply for both calls: triage accepts the chest-pain source, vitals does not (not a vitals source).
    reply = answer_with_source("low", finding_type="chest_pain", source_id=CHEST_PAIN_US)
    fake_bedrock.replies = {"haiku": [reply, reply]}

    data = assess(client, {
        "symptoms": "mild chest discomfort",
        "vitals": {"heart_rate": 72, "client_reading_id": "r_two"},
        "context": {"country": "US"},
    })

    assert data["motors_used"] == ["triage", "vitals"]
    assert [source["motor"] for source in data["sources"]] == data["motors_used"]
    assert [(source["status"], source["link"]) for source in data["sources"]] == [
        ("matched", CHEST_PAIN_LINK), ("pending_medical_review", None),
    ]


def test_failed_motor_has_no_source(client, use_bedrock, fake_bedrock, monkeypatch):  # noqa: F811
    async def failing_call_motor(client, name, motor_input):
        return None, 1

    monkeypatch.setattr(orchestrator_routes, "call_motor", failing_call_motor)

    data = assess(client, {"symptoms": "tired", "context": {"country": "US"}})

    assert (data["motors_failed"], data["sources"]) == (["triage"], [])


def test_full_citation_and_doctor_fields_are_never_sent_to_the_app(client, use_bedrock, fake_bedrock, queued_jobs):  # noqa: F811
    fake_bedrock.replies = {"haiku": [answer_with_source("low", finding_type="chest_pain", source_id=CHEST_PAIN_US)]}

    data = assess(client, {"symptoms": "mild chest discomfort", "context": {"country": "US"}})

    for field in ["reference", "source_id", "finding_type", "evidence_level"]:
        assert field not in data
        assert field not in data["sources"][0]
    (_, job), = queued_jobs
    (row,) = job.motor_results
    text = json.dumps(data)
    assert row.reference not in text  # the full citation saved for doctors
    assert library_source(CHEST_PAIN_US)["title"] not in text
    assert CHEST_PAIN_US not in text and "chest_pain" not in text and "clinical_guideline" not in text


def test_source_label_does_not_change_the_explanation_or_the_save_job(client, use_bedrock, fake_bedrock, queued_jobs):  # noqa: F811
    fake_bedrock.replies = {"haiku": [answer_with_source("low", finding_type="chest_pain", source_id=CHEST_PAIN_US)]}

    data = assess(client, {"symptoms": "mild chest discomfort", "context": {"country": "US"}})

    assert data["explanation"] == "Worth reviewing with a healthcare professional."
    assert "according to" not in data["explanation"]
    (_, job), = queued_jobs
    assert "sources" not in job.model_dump_json()


def test_parse_answer_ignores_text_around_the_json():
    # Seen from Haiku on Bedrock: the JSON in a code fence, then a recommendation paragraph.
    text = "```json\n" + answer_with_source("low", finding_type="image_quality") + "\n```\n\n**Recommendation:** Retake the photo."
    answer = runner.parse_answer(text)
    assert (answer["risk_level"], answer["finding_type"]) == ("low", "image_quality")
