# FDA wording rules: the app must never sound like it is diagnosing or prescribing.
# Replaces unsafe explanations and adds the disclaimer to every response.

from app.shared.contracts import DISCLAIMER_ID

# Edit this list to ban more phrases. Matching ignores upper/lower case.
BANNED_PHRASES = [
    "you have",
    "this is",
    "go to the er",
    "emergency room",
    "emergency services",
    "call 911",
    "take this medicine",
    "diagnosis is",
]

DISCLAIMER_TEXT = "This result is not a diagnosis. Always consult your doctor."

SAFE_EXPLANATION = "Based on the information shared, it may help to review this with a healthcare professional."


def contains_banned_phrase(text: str) -> bool:
    text = text.lower()
    return any(phrase in text for phrase in BANNED_PHRASES)


def apply_fda_rules(response: dict) -> dict:
    """Clean the explanation if needed and attach the disclaimer. Returns the same dict."""
    if contains_banned_phrase(response["explanation"]):
        # We replace the whole text rather than cut out words, so no half-sentence diagnosis slips through.
        response["explanation"] = SAFE_EXPLANATION

    response["disclaimer_id"] = DISCLAIMER_ID
    response["disclaimer"] = DISCLAIMER_TEXT
    return response
