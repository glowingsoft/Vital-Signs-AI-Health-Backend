# Which AI model the triage motor uses and how it calls it. Models and prices: app/configurations/.

from typing import Optional

from app import config
from app.medical_sources.library import prompt_block
from app.configurations.routing import ROUTES, pick_model
from app.motors.runner import JSON_ANSWER_INSTRUCTIONS, ModelResult, run_chain, stub_result

MOTOR = "triage"

SYSTEM_PROMPT = (
    "You help a wellness app decide how soon a person should talk to a healthcare professional "
    "about the symptoms they describe. " + JSON_ANSWER_INSTRUCTIONS
)


def get_model(data: dict, escalate: bool = False) -> str:
    """Default model, or the escalation model for red flags, low confidence or conflicting answers."""
    return pick_model(MOTOR, escalate)


def run_model(data: dict, escalate: bool = False, country: Optional[str] = None) -> ModelResult:
    if not config.USE_BEDROCK:
        return stub_result()
    # Only the symptom text is sent: the user id and the rest of the request never reach the model.
    messages = [{"role": "user", "content": [{"text": data.get("message", "")}]}]
    return run_chain(
        MOTOR, get_model(data, escalate), messages,
        SYSTEM_PROMPT + prompt_block(MOTOR, country),  # + the trusted sources for this country and motor
        ROUTES[MOTOR].max_output_tokens, escalated=escalate,
    )
