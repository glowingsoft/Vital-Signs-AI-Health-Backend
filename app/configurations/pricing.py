# Cost of one AI call in USD, from the token counts Bedrock returns and the prices in models.py.

from app.configurations.models import MODELS


def cost_usd(model_key: str, input_tokens: int, output_tokens: int) -> float:
    model = MODELS[model_key]
    return (input_tokens * model.input_usd_per_million + output_tokens * model.output_usd_per_million) / 1_000_000
