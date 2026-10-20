# Every AI model the motors may call, with its Bedrock ID and price.
#
# IDs: US cross-region inference profiles ("us."), called from us-west-2. Never use "global." IDs:
# they can process data outside the U.S.
# Prices: USD per 1 million tokens, from the AWS Bedrock pricing page, US West (Oregon),
# Geo and In-region, Standard tier, checked 19 Oct 2026 (same as docs/AI-Cost-Estimate.docx).

from dataclasses import dataclass
from typing import Dict


# frozen=True = the values cannot be changed by accident while the app runs.
@dataclass(frozen=True)
class ModelConfig:
    key: str  # short name used in routing.py, e.g. "haiku"
    model_id: str  # Bedrock model ID; saved as model_name in ai.motor_results and ai.ai_usage
    input_usd_per_million: float
    output_usd_per_million: float
    supports_images: bool
    version: str  # saved as model_version


MODELS: Dict[str, ModelConfig] = {
    model.key: model
    for model in [
        ModelConfig("haiku", "us.anthropic.claude-haiku-5-5", 0.11, 0.55, True, "claude-haiku-5.5"),
        ModelConfig("sonnet", "us.anthropic.claude-sonnet-5-5", 2.20, 11.00, True, "claude-sonnet-5.5"),
        ModelConfig("nova_pro", "us.amazon.nova-pro-v1:0", 0.80, 3.20, True, "nova-pro-v1:0"),
        ModelConfig("nova_lite", "us.amazon.nova-lite-v1:0", 0.06, 0.24, True, "nova-lite-v1:0"),
        ModelConfig("llama", "us.meta.llama3-3-70b-instruct-v1:0", 0.72, 0.72, False, "llama3.3-70b-instruct-v1:0"),
    ]
}

# Fail at startup if someone adds a "global." ID by mistake.
for _model in MODELS.values():
    if not _model.model_id.startswith("us."):
        raise ValueError(f"{_model.key}: only US model IDs are allowed, got '{_model.model_id}'")
