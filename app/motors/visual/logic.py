# Visual motor STUB: does not look at the image yet, it only confirms one was received.
# No AI yet; this only returns correctly shaped fake answers.

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

    # Without real image analysis we stay cautious: "medium" with low confidence.
    return MotorOutput(
        risk_level="medium",
        confidence_score=0.4,
        explanation="Your image was received. A healthcare professional can review it for you.",
        disclaimer_id=DISCLAIMER_ID,
        finding_type="image_received",
        evidence_level="stub",
        model_name="stub",
    )
