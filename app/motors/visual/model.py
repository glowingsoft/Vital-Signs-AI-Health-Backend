# Which AI model the visual motor uses and how it calls it. Models and prices: app/configurations/.
# Only models that can read images are used (llama is never in the visual route).

from typing import Optional

from app import config
from app.configurations.routing import ROUTES, VISUAL_MAX_JSON_RETRIES, pick_model
from app.motors.runner import ModelResult, run_chain, stub_result
from app.shared.images import decode_image
from app.shared.prompts import system_prompt

MOTOR = "visual"


def image_block(image: str) -> dict:
    """A Converse image block from a base64 image. Raises InvalidImage (app/shared/images.py)."""
    raw, image_format = decode_image(image)
    return {"image": {"format": image_format, "source": {"bytes": raw}}}


def get_model(data: dict, escalate: bool = False) -> str:
    """Default model (Haiku), or the escalation model (Sonnet) when Haiku fails or is unsure."""
    return pick_model(MOTOR, escalate)


def run_model(
    data: dict,
    escalate: bool = False,
    deadline: Optional[float] = None,
    use_fallbacks: bool = True,
    country: Optional[str] = None,
) -> ModelResult:
    """One chain of calls. `deadline` (time.monotonic()) lets every step share config.motor_budget_seconds("visual");
    use_fallbacks=False calls only the first model. `country` picks the trusted sources added to the prompt. Raises AllModelsFailed when no model gives a valid
    JSON answer, even after VISUAL_MAX_JSON_RETRIES retries each."""
    block = image_block(data.get("image") or "")  # checked first, so a bad image is refused even with Bedrock off
    if not config.USE_BEDROCK:
        return stub_result()
    messages = [{"role": "user", "content": [block, {"text": "Describe this image."}]}]
    return run_chain(
        MOTOR,
        get_model(data, escalate),
        messages,
        system_prompt(MOTOR, country),
        ROUTES[MOTOR].max_output_tokens,
        escalated=escalate,
        image_count=1,
        json_answer=True,
        deadline=deadline,
        max_json_retries=VISUAL_MAX_JSON_RETRIES,
        use_fallbacks=use_fallbacks,
        retry_without_images=True,  # the model only needs its own reply to fix the JSON format
    )
