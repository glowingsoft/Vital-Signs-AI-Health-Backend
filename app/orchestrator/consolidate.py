# Combines the answers from several motors into one result for the app.
# Rule: the highest risk level wins (low < medium < high < critical).

from typing import Dict, List

from app.shared.contracts import RISK_ORDER, MotorOutput

# Used when every motor failed: we never return "low" when we know nothing.
ALL_FAILED_RESULT = {
    "risk_level": "medium",
    "confidence_score": 0.0,
    "explanation": "We could not complete the check right now. Please contact a healthcare professional.",
}


def consolidate(results: Dict[str, MotorOutput]) -> dict:
    """results = {"vitals": MotorOutput(...), ...} for the motors that answered."""
    if not results:
        return dict(ALL_FAILED_RESULT)  # dict(...) = a copy, so callers can't change the original

    # Sort the answers from most to least serious, so the worst one comes first.
    answers: List[MotorOutput] = sorted(
        results.values(), key=lambda answer: RISK_ORDER.index(answer.risk_level), reverse=True
    )
    top_answer = answers[0]

    return {
        # Highest risk wins: the first answer after sorting is the most serious one.
        "risk_level": top_answer.risk_level,
        # Confidence of the answer that decided the risk level.
        "confidence_score": top_answer.confidence_score,
        # Keep every motor's explanation, most serious first, so the user sees the full picture.
        "explanation": " ".join(answer.explanation for answer in answers),
    }
