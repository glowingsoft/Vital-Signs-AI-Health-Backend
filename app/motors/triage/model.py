# Which AI model the triage motor uses and how it calls it. Models and prices: app/configurations/.

from typing import Optional

from app import config
from app.configurations.routing import ROUTES, pick_model
from app.motors.runner import ModelResult, run_chain, stub_result
from app.shared.prompts import system_prompt

MOTOR = "triage"


def get_model(data: dict, escalate: bool = False) -> str:
    """Default model, or the escalation model for a high or critical answer or low confidence."""
    return pick_model(MOTOR, escalate)


def run_model(
    data: dict, escalate: bool = False, deadline: Optional[float] = None, country: Optional[str] = None
) -> ModelResult:
    """One chain of calls. `deadline` (time.monotonic()) lets the first call and the escalation share
    config.motor_budget_seconds("triage"). `country` picks the trusted sources added to the prompt.
    Raises AllModelsFailed when no model gives a valid JSON answer."""
    if not config.USE_BEDROCK:
        return stub_result()
    # Only the symptom text is sent: the user id and the rest of the request never reach the model.
    messages = [{"role": "user", "content": [{"text": data.get("message", "")}]}]
    return run_chain(
        MOTOR,
        get_model(data, escalate),
        messages,
        system_prompt(MOTOR, country),
        ROUTES[MOTOR].max_output_tokens,
        escalated=escalate,
        json_answer=True,
        deadline=deadline,
    )
