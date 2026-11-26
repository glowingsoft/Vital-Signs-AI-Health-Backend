# The source of each motor answer (app/medical_sources/library.check_source and rule_source_id):
#   source_id is saved only when the pick is an ALLOWED source: status in MEDICAL_SOURCE_STATUSES (default
#   "active"), the patient's country, and this motor. Anything else -> source_status "pending_medical_review".
# The seed file is used as the library (no database), with its statuses changed per test.

import pytest

from app import config
from app.medical_sources import library
from app.motors.triage.logic import analyze as triage
from app.motors.visual.logic import analyze as visual
from app.motors.vitals.logic import analyze as vitals
from app.orchestrator.build_job import build_motor_result
from app.shared.contracts import MotorOutput


@pytest.fixture
def sources(monkeypatch):
    """The seed sources; call it with ids to make those "active" (all others keep their seed status, draft)."""
    rows = library.read_from_file()

    def activate(*ids):
        for row in rows:
            if row["id"] in ids:
                row["status"] = "active"
        return rows

    monkeypatch.setattr(library, "load_sources", lambda: rows)
    monkeypatch.setattr(config, "MEDICAL_SOURCE_STATUSES", ("active",))
    return activate


def test_ai_pick_is_saved_only_when_it_is_an_allowed_source(sources):
    sources("US-CDC-STROKE")
    matched = library.check_source("US-CDC-STROKE", "triage", "US")
    assert (matched["source_id"], matched["source_status"]) == ("US-CDC-STROKE", "matched")
    assert matched["reference"].endswith(matched["source_link"]) and matched["source_label"]

    for picked, motor, country in [
        ("US-AHA-CHESTPAIN-2021", "triage", "US"),  # exists, but still a draft
        ("US-CDC-STROKE", "vitals", "US"),  # active, but not a vitals source
        ("US-CDC-STROKE", "triage", "MX"),  # active, but for the other country
        ("US-MADE-UP-2099", "triage", "US"),  # not in the library at all
        ("none", "triage", "US"), (None, "triage", "US"), ("US-CDC-STROKE", "triage", None),
    ]:
        result = library.check_source(picked, motor, country)
        assert (result["source_id"], result["source_status"], result["reference"]) == (
            None, "pending_medical_review", None,
        ), (picked, motor, country)


def test_country_spellings(sources):
    sources("MX-CENETEC-SCASEST-2018")
    for country in ("MX", "mx", "Mexico", "México"):
        assert library.check_source("MX-CENETEC-SCASEST-2018", "triage", country)["source_status"] == "matched"


def test_triage_stub_picks_the_source_its_keywords_point_to(sources):
    sources("US-AHA-CHESTPAIN-2021", "US-CDC-HEARTATTACK")
    out = triage({"message": "I have chest pain and shortness of breath"}, country="US")
    # Both cover chest pain; the heart attack page also covers breathing difficulty, so it covers more.
    assert (out.source_id, out.source_status, out.risk_level) == ("US-CDC-HEARTATTACK", "matched", "critical")
    assert out.evidence_level and out.evidence_level != "stub"


def test_triage_without_an_active_source_is_pending(sources):
    sources()  # everything stays draft, as in the seed file
    out = triage({"message": "chest pain"}, country="US")
    assert (out.source_id, out.source_status, out.reference) == (None, "pending_medical_review", None)


def test_draft_sources_can_be_allowed_for_a_demo(sources, monkeypatch):
    sources()
    monkeypatch.setattr(config, "MEDICAL_SOURCE_STATUSES", ("active", "draft", "proposed_addition"))
    assert triage({"message": "chest pain"}, country="MX").source_id == "MX-CENETEC-SCASEST-2018"


def test_vitals_stub_picks_the_source_for_the_most_serious_reading(sources):
    sources("US-AHA-HBP-2025", "US-AHA-BRADYCARDIA-2018")
    # Heart rate 105 = medium, blood pressure 185/125 = critical: the blood pressure source wins.
    out = vitals({"heart_rate": 105, "systolic": 185, "diastolic": 125}, country="US")
    assert (out.risk_level, out.source_id, out.source_status) == ("critical", "US-AHA-HBP-2025", "matched")


def test_visual_stub_uses_the_wound_source(sources):
    sources("MX-CENETEC-UPP-2015")
    out = visual({"image": "uploads/photo.jpg"}, country="MX")
    assert (out.source_id, out.source_status) == ("MX-CENETEC-UPP-2015", "matched")


def test_ai_answer_with_an_unknown_source_is_ignored(sources, monkeypatch):
    sources("US-AHA-CHESTPAIN-2021")
    from app.motors import runner
    from app.motors.triage import logic

    answer = '{"risk_level": "high", "confidence_score": 0.7, "explanation": "Worth reviewing.", ' \
             '"finding_type": "chest_pain", "source_id": "US-FAKE-JOURNAL-2024"}'
    monkeypatch.setattr(logic, "run_model", lambda data, escalate=False, country=None: runner.ModelResult(
        text=answer, model_key="haiku", model_id="haiku", model_version=None, escalated=False, fallback_used=False,
        is_retry=False, input_tokens=1, output_tokens=1, image_count=0, cost_usd=0.0, latency_ms=1,
    ))
    out = triage({"message": "chest pain"}, country="US")
    assert (out.source_id, out.source_status, out.finding_type) == (None, "pending_medical_review", "chest_pain")


def test_source_reaches_the_motor_results_row(sources):
    sources("US-CDC-STROKE")
    from uuid import uuid4
    output = MotorOutput(risk_level="high", confidence_score=0.7, explanation="x", disclaimer_id="disc_v1",
                         **library.check_source("US-CDC-STROKE", "triage", "US"))
    row = build_motor_result(uuid4(), "triage", output, 10)
    assert (row.source_id, row.source_status) == ("US-CDC-STROKE", "matched")
