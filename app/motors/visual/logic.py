# Visual motor: the routed AI model describes the image (app/motors/visual/model.py).
# With USE_BEDROCK off, or without a real image, it only confirms one was received (the stub).

from app.motors.runner import parse_answer, usage_fields
from app.motors.visual.model import run_model
from app.shared.contracts import DISCLAIMER_ID, MotorOutput


def analyze(data: dict) -> MotorOutput:
    if not data.get("image"):
        return MotorOutput(
            risk_level="low",
            confidence_score=0.1,
            explanation="No image was received, so no visual check was made.",
            disclaimer_id=DISCLAIMER_ID,
            model_name="stub",  # no AI model yet; token and cost fields keep their default of 0
        )

    # The phone's first check (stage1) flagging the image sends it to the escalation model.
    stage1_hint = (data.get("stage1") or {}).get("risk_hint")
    result = run_model(data, escalate=stage1_hint in ("high", "critical"))
    answer = parse_answer(result.text)

    if answer is None:
        # Without image analysis we stay cautious: "medium" with low confidence.
        return MotorOutput(
            risk_level="medium",
            confidence_score=0.4,
            explanation="Your image was received. A healthcare professional can review it for you.",
            disclaimer_id=DISCLAIMER_ID,
            finding_type="image_received",
            evidence_level="stub",
            **usage_fields(result),  # a model that answered off-format still cost money
        )
    return MotorOutput(
        risk_level=answer["risk_level"],
        confidence_score=answer["confidence_score"],
        explanation=answer["explanation"],
        disclaimer_id=DISCLAIMER_ID,
        finding_type="image_description",
        evidence_level="stub",
        **usage_fields(result),
    )
