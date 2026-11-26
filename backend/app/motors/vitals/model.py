# Which AI model the vitals motor uses and how it calls it. Models and prices: app/configurations/.
# The model only writes a plain-language explanation; the risk level is never decided in the cloud.

from typing import Optional

from app import config
from app.configurations.routing import ROUTES, pick_model
from app.medical_sources.library import prompt_block
from app.motors.runner import VITALS_ANSWER_INSTRUCTIONS, ModelResult, run_chain, stub_result

MOTOR = "vitals"

SYSTEM_PROMPT = (
    "You explain vital-sign readings in plain language for a wellness app, in one or two short sentences. "
    "The risk level has already been decided by the app's rules; your explanation must match it. "
    + VITALS_ANSWER_INSTRUCTIONS
)

# Only these readings are sent to the model.
READINGS = ["heart_rate", "spo2", "systolic", "diastolic", "respiratory_rate"]


def get_model(data: dict, escalate: bool = False) -> str:
    """Always the default model: vitals never escalates."""
    return pick_model(MOTOR, escalate=False)


def run_model(data: dict, escalate: bool = False, country: Optional[str] = None) -> ModelResult:
    if not config.USE_BEDROCK:
        return stub_result()
    readings = ", ".join(f"{name}={data[name]}" for name in READINGS if data.get(name) is not None)
    messages = [{"role": "user", "content": [{"text": f"Readings: {readings}"}]}]
    return run_chain(
        MOTOR, get_model(data), messages,
        SYSTEM_PROMPT + prompt_block(MOTOR, country),  # + the trusted sources for this country and motor
        ROUTES[MOTOR].max_output_tokens,
    )
