# Which AI model the triage motor uses and how it calls it. Models and prices: app/configurations/.

from typing import Optional

from app import config
from app.configurations.routing import ROUTES, pick_model
from app.motors.runner import JSON_ANSWER_INSTRUCTIONS, ModelResult, run_chain, stub_result

MOTOR = "triage"

SYSTEM_PROMPT = (
    "You help a wellness app decide how soon a person should talk to a healthcare professional "
    "about the symptoms they describe. The symptoms may be written in English or Spanish; understand both, "
    "and write the explanation in the same language as the symptoms. "
    'Chest pain, difficulty breathing, loss of consciousness, seizure and vomiting blood must always be "critical" '
    '(in Spanish, for example: dolor en el pecho, dificultad para respirar, pérdida del conocimiento, convulsión, '
    "vomitar sangre). " + JSON_ANSWER_INSTRUCTIONS
)


def get_model(data: dict, escalate: bool = False) -> str:
    """Default model, or the escalation model for a high or critical answer or low confidence."""
    return pick_model(MOTOR, escalate)


def run_model(data: dict, escalate: bool = False, deadline: Optional[float] = None) -> ModelResult:
    """One chain of calls. `deadline` (time.monotonic()) lets the first call and the escalation share
    MOTOR_TIMEOUT_SECONDS. Raises AllModelsFailed when no model gives a valid JSON answer."""
    if not config.USE_BEDROCK:
        return stub_result()
    # Only the symptom text is sent: the user id and the rest of the request never reach the model.
    messages = [{"role": "user", "content": [{"text": data.get("message", "")}]}]
    return run_chain(
        MOTOR,
        get_model(data, escalate),
        messages,
        SYSTEM_PROMPT,
        ROUTES[MOTOR].max_output_tokens,
        escalated=escalate,
        json_answer=True,
        deadline=deadline,
    )
