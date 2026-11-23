# Tests for app/orchestrator/build_job.py: the rows built from one /ai/assess call.
# No network, Redis or database needed.

from uuid import uuid4

from app.orchestrator.build_job import build_save_assessment_job
from app.shared.contracts import AssessRequest, MotorOutput


def motor_output(risk_level: str) -> MotorOutput:
    return MotorOutput(
        risk_level=risk_level, confidence_score=0.7, explanation="raw motor text", disclaimer_id="disc_v1"
    )


def final_response(motors_used, motors_failed=()) -> dict:
    # Shape of the response after consolidate() + apply_fda_rules().
    return {
        "risk_level": "high",
        "confidence_score": 0.7,
        "explanation": "filtered text",
        "disclaimer_id": "disc_v1",
        "disclaimer": "This result is not a diagnosis. Always consult your doctor.",
        "motors_used": list(motors_used),
        "motors_failed": list(motors_failed),
    }


def test_all_inputs_build_every_row():
    prediction_id = uuid4()
    request = AssessRequest(
        user_id="u_123",
        symptoms="headache",
        image="base64-image-bytes",
        vitals={"heart_rate": 130, "spo2": 93, "client_reading_id": "r_1"},
        context={"country": "US", "state": "CA", "app_version": "1.0.0"},
    )
    outputs = {"triage": motor_output("medium"), "visual": motor_output("medium"), "vitals": motor_output("high")}
    latencies = {"triage": 10, "visual": 20, "vitals": 30}

    job = build_save_assessment_job(prediction_id, request, final_response(outputs), outputs, latencies, 50)

    # Reading: labelled by the user, because symptoms were sent.
    assert job.reading.label == "reported_symptom"
    assert job.reading.label_source == "user"
    assert job.reading.systolic_bp is None and job.reading.heart_rate == 130
    assert job.reading.country == "US"

    # Assessment: same id as prediction_id, points to the reading, image bytes not stored.
    assert job.assessment.id == prediction_id
    assert job.assessment.reading_id == job.reading.id
    assert job.assessment.input["image"] == "omitted"
    assert job.assessment.input["symptoms"] == "headache"
    assert job.assessment.input_types == ["text", "image", "vitals"]
    assert job.assessment.label == "reported_symptom"
    assert job.assessment.label_source == "system"
    assert job.assessment.explanation == "filtered text"
    assert job.assessment.total_latency_ms == 50

    # One motor_results row and one ai_usage row per motor that answered.
    assert [row.motor for row in job.motor_results] == ["triage", "visual", "vitals"]
    assert [row.motor for row in job.ai_usage] == ["triage", "visual", "vitals"]
    assert all(row.assessment_id == prediction_id for row in job.motor_results + job.ai_usage)
    assert job.motor_results[0].explanation == "raw motor text"  # before the FDA filter
    assert job.motor_results[2].latency_ms == 30
    assert job.ai_usage[0].model_name == "stub"

    # Audit row describes the creation of this assessment.
    assert job.audit_log.resource_id == str(prediction_id)
    assert job.audit_log.actor_role == "patient"
    assert job.audit_log.after == {"risk_level": "high", "label": "reported_symptom"}

    # Every row got its own id up front, so a retried job inserts the same ids.
    ids = [job.reading.id, job.audit_log.id] + [row.id for row in job.motor_results + job.ai_usage]
    assert len(set(ids)) == len(ids)


def test_vitals_only_is_labelled_normal():
    request = AssessRequest(
        user_id="u_123", vitals={"heart_rate": 70, "client_reading_id": "r_2"}, context={"country": "US"}
    )
    outputs = {"vitals": motor_output("low")}

    job = build_save_assessment_job(uuid4(), request, final_response(outputs), outputs, {"vitals": 5}, 5)

    assert job.reading.label == "normal"
    assert job.assessment.label == "normal"
    assert "image" not in job.assessment.input


def test_no_vitals_means_no_reading_and_failed_motors_get_no_rows():
    request = AssessRequest(user_id="u_123", symptoms="cough", image="img")
    outputs = {"triage": motor_output("medium")}  # visual failed

    job = build_save_assessment_job(
        uuid4(), request, final_response(["triage"], ["visual"]), outputs, {"triage": 5}, 5
    )

    assert job.reading is None
    assert job.assessment.reading_id is None
    assert job.assessment.motors_failed == ["visual"]
    assert len(job.motor_results) == 1
    assert len(job.ai_usage) == 1
<<<<<<< Updated upstream
=======


def test_image_key_is_saved_in_place_of_the_image():
    request = AssessRequest(image=PNG_IMAGE, image_key="scans/1234455.png")
    outputs = {"visual": motor_output("medium")}

    job = build_save_assessment_job(
        uuid4(), request, final_response(outputs), outputs, {"visual": 5}, 5, user_id="u_123"
    )

    assert job.assessment.input == {"image": "scans/1234455.png"}  # the key, never the image bytes
    assert job.assessment.input_types == ["image"]


@pytest.mark.parametrize("bad_key", [
    "../other-user/1.png", "1234455.exe", "", "/1234455.png", "a b.png", "https://bucket.s3/1.png",
])
def test_unsafe_image_key_is_refused(bad_key):
    with pytest.raises(ValueError):
        AssessRequest(image=PNG_IMAGE, image_key=bad_key)


def test_image_key_without_image_is_refused():
    with pytest.raises(ValueError):
        AssessRequest(image_key="1234455.png")


def test_audio_key_is_saved_with_the_symptoms():
    request = AssessRequest(symptoms="chest pain since morning", audio_key="voice/1794548444899-a1b2c3.m4a")
    outputs = {"triage": motor_output("high")}

    job = build_save_assessment_job(
        uuid4(), request, final_response(outputs), outputs, {"triage": 5}, 5, user_id="u_123"
    )

    assert job.assessment.input == {"symptoms": "chest pain since morning", "audio": "voice/1794548444899-a1b2c3.m4a"}
    assert job.assessment.input_types == ["text", "audio"]


@pytest.mark.parametrize("bad_key", ["../x.m4a", "voice.exe", "photo.png", "https://bucket.s3/1.m4a", ""])
def test_unsafe_audio_key_is_refused(bad_key):
    with pytest.raises(ValueError):
        AssessRequest(symptoms="cough", audio_key=bad_key)


def test_audio_key_without_symptoms_is_refused():
    with pytest.raises(ValueError):
        AssessRequest(audio_key="1234455.m4a")


@pytest.mark.parametrize("role,expected", [
    (UserType.Admin, "admin"), (UserType.User, "patient"), (UserType.Doctor, "doctor"),
    (UserType.Supervisor, "supervisor"), (UserType.Labeler, "labeler"), (UserType.Developer, "developer"),
])
def test_actor_role_for_each_role(role, expected):
    assert actor_role_for(role) == expected


def test_guest_never_gets_an_actor_role():
    with pytest.raises(ValueError):
        actor_role_for(UserType.Guest)
>>>>>>> Stashed changes
