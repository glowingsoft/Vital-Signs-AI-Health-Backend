# Combines the answers from several motors into one result for the app.
# Rule: the highest risk level wins (low < medium < high < critical).

from typing import Dict, List, Optional

from app.medical_sources.constants import DEFAULT_PENDING_REVIEW_COUNTRY, PENDING_REVIEW_LABELS
from app.medical_sources.library import MATCHED, PENDING_MEDICAL_REVIEW, normalize_country
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


def pending_review_label(country: Optional[str]) -> str:
    """"Pending medical review" in the patient's language: Spanish for MX, English for US or an unknown country."""
    code = normalize_country(country)
    return PENDING_REVIEW_LABELS.get(code, PENDING_REVIEW_LABELS[DEFAULT_PENDING_REVIEW_COUNTRY])


def sources_for_app(results: Dict[str, MotorOutput], country: Optional[str]) -> List[dict]:
    """One {motor, status, label, link} per motor that answered, in the same order as results (= motors_used).
    Only the patient label and the library's own link are passed on; the link is never built or changed here.
    No matched source (pending review, or nothing assessed, e.g. "ai_unavailable") -> default label, no link."""
    sources = []
    for motor, answer in results.items():
        if answer.source_status == MATCHED and answer.source_label:
            sources.append({"motor": motor, "status": MATCHED, "label": answer.source_label, "link": answer.source_link})
        else:
            sources.append({
                "motor": motor, "status": PENDING_MEDICAL_REVIEW, "label": pending_review_label(country), "link": None,
            })
    return sources
