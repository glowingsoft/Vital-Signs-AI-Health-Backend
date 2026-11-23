# Which model each motor uses: default, escalation (a stronger model when needed) and the
# fallbacks tried in order when a model fails or times out (Proposal, AI model routing and cost).
# Change ROUTING_VERSION whenever this file changes.

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from app.configurations.models import MODELS

ROUTING_VERSION = "2026-10-19"


@dataclass(frozen=True)
class MotorRoute:
    default: str
    escalation: Optional[str]  # None = this motor never escalates
    fallbacks: Tuple[str, ...]  # tried in this order
    max_output_tokens: int


ROUTES: Dict[str, MotorRoute] = {
    "triage": MotorRoute(default="haiku", escalation="sonnet", fallbacks=("nova_pro", "llama"), max_output_tokens=1500),
    # Llama cannot read images, so it is never in the visual route.
    "visual": MotorRoute(default="haiku", escalation="sonnet", fallbacks=("nova_pro",), max_output_tokens=300),
    "vitals": MotorRoute(default="haiku", escalation=None, fallbacks=("nova_lite",), max_output_tokens=200),
}


def pick_model(motor: str, escalate: bool = False) -> str:
    """The model key a motor starts with. escalate=True gives the escalation model, if the motor has one."""
    route = ROUTES[motor]
    if escalate and route.escalation:
        return route.escalation
    return route.default


def model_chain(motor: str, first: str, has_image: bool = False) -> Tuple[str, ...]:
    """The models to try in order: `first`, then the fallbacks. With an image, only models that read images."""
    chain = [first] + [key for key in ROUTES[motor].fallbacks if key != first]
    if has_image:
        chain = [key for key in chain if MODELS[key].supports_images]
    return tuple(chain)


# Fail at startup on a typo or an image motor that routes to a text-only model.
for _motor, _route in ROUTES.items():
    for _key in (_route.default, _route.escalation, *_route.fallbacks):
        if _key is not None and _key not in MODELS:
            raise ValueError(f"routing for {_motor}: unknown model '{_key}'")
for _key in (ROUTES["visual"].default, ROUTES["visual"].escalation, *ROUTES["visual"].fallbacks):
    if not MODELS[_key].supports_images:
        raise ValueError(f"routing for visual: '{_key}' cannot read images")
