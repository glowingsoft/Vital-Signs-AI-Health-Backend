# Checks that every model in app/configurations/models.py answers on Amazon Bedrock.
# Sends one short, made-up message to each model and prints whether it worked, how fast it was
# and what it cost. Uses synthetic text only: never put real patient data in here.
#
# Run from the repo root (AWS keys and region come from .env):
#     python -m scripts.test_bedrock_models
# Or inside Docker:
#     docker compose run --rm triage python -m scripts.test_bedrock_models

import sys

from app import config
from app.configurations.models import MODELS
from app.configurations.pricing import cost_usd
from app.shared import bedrock

TEST_MESSAGE = "This is a connection test. Reply with the single word: OK"

# A first call can be slow while AWS sets up the model, so allow more time than the motors do.
TIMEOUT_SECONDS = 30

# Common AWS errors and what to do about them.
HINTS = {
    "UnrecognizedClientException": "The access key is wrong. Check AWS_ACCESS_KEY_ID in .env.",
    "InvalidSignatureException": "The secret key is wrong. Check AWS_SECRET_ACCESS_KEY in .env.",
    "ExpiredTokenException": "The AWS login has expired. Create a new access key.",
    "AccessDeniedException": "No permission, or the model is not enabled. Check the user's Bedrock policy "
    "and the model in the Bedrock console (us-west-2). Anthropic models may need the use-case form.",
    "ResourceNotFoundException": "The model is not available. Check it is enabled in the Bedrock console.",
    "ValidationException": "AWS did not accept the model ID. Check model_id in app/configurations/models.py.",
    "ThrottlingException": "Too many requests. Wait a minute and run again.",
    "NoCredentialsError": "No AWS keys found. Add AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY to .env.",
    "timed out": "No answer in time. Check your internet and run again.",
}


def hint_for(error: str) -> str:
    for code, hint in HINTS.items():
        if code in error:
            return hint
    return ""


def main() -> int:
    print(f"Region: {config.AWS_REGION}   USE_BEDROCK in .env: {config.USE_BEDROCK}")
    if not config.USE_BEDROCK:
        print("Note: USE_BEDROCK is false, so the app still uses stub answers. This test calls AWS anyway.")
    print()
    print(f"{'Model':<11}{'Result':<8}{'Time':>8}{'Tokens in/out':>15}{'Cost USD':>12}   Answer / problem")
    print("-" * 95)

    failed = 0
    messages = [{"role": "user", "content": [{"text": TEST_MESSAGE}]}]
    for key, model in MODELS.items():
        try:
            answer = bedrock.converse(key, messages, system="", max_tokens=20, timeout=TIMEOUT_SECONDS)
        except Exception as error:  # show every failure and carry on with the next model
            failed += 1
            message = str(error).replace("\n", " ")
            print(f"{key:<11}{'FAIL':<8}{'':>8}{'':>15}{'':>12}   {message[:200]}")
            hint = hint_for(f"{type(error.__cause__).__name__} {message}")
            if hint:
                print(f"{'':<54}-> {hint}")
            continue
        cost = cost_usd(key, answer.input_tokens, answer.output_tokens)
        tokens = f"{answer.input_tokens}/{answer.output_tokens}"
        reply = answer.text.strip().replace("\n", " ")[:40]
        print(f"{key:<11}{'OK':<8}{answer.latency_ms:>6}ms{tokens:>15}{cost:>12.6f}   {reply}")

    print()
    if failed:
        print(f"{failed} of {len(MODELS)} models failed. Fix the problems above and run again.")
        return 1
    print(f"All {len(MODELS)} models answered. Bedrock is ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
