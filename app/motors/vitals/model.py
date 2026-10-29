# Which AI model the vitals motor uses and how it calls it. Models and prices: app/configurations/.
# The model only writes a plain-language explanation; the risk level is never decided in the cloud.

from app import config
from app.configurations.routing import ROUTES, pick_model
from app.motors.runner import ModelResult, run_chain, stub_result

MOTOR = "vitals"

SYSTEM_PROMPT = (
    "You explain vital-sign readings in plain language for a wellness app, in one or two short sentences. "
    "The risk level has already been decided by the app's rules; your explanation must match it. "
    'Never call the readings normal or fine when the risk level is "medium", "high" or "critical", '
    "and never state a different risk level. "
    "Never diagnose, never name a condition, medicine or specific medical action, and never sound certain. "
    "Always recommend a healthcare professional."
)

# Only these readings (and the risk level) are sent to the model. HRV and sleep are only saved.
READINGS = ["heart_rate", "spo2", "systolic", "diastolic", "respiratory_rate"]


def get_model(data: dict, escalate: bool = False) -> str:
    """Always the default model: vitals never escalates."""
    return pick_model(MOTOR, escalate=False)


def run_model(data: dict, risk_level: str, escalate: bool = False) -> ModelResult:
    """An explanation of `risk_level` (decided by the rules in logic.py). Raises AllModelsFailed."""
    if not config.USE_BEDROCK:
        return stub_result()
    readings = ", ".join(f"{name}={data[name]}" for name in READINGS if data.get(name) is not None)
    text = f"Risk level (already decided, do not change it): {risk_level}. Readings: {readings}"
    messages = [{"role": "user", "content": [{"text": text}]}]
    return run_chain(MOTOR, get_model(data), messages, SYSTEM_PROMPT, ROUTES[MOTOR].max_output_tokens)
