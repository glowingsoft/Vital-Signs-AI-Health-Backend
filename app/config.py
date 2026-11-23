# Reads every environment variable in one place.
# Other files import from here instead of calling os.environ themselves (like Django's settings.py).

import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines from .env into os.environ, so no 'source .env' is needed."""
    if not path.exists():
        return
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        # KEY="value" or KEY='value': the quotes are not part of the value (same as Docker Compose and dotenv).
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value  # a key listed twice: the last one wins, same as "source .env"
    for key, value in values.items():
        # setdefault = a variable already set in the shell wins over the file.
        os.environ.setdefault(key, value)


load_env_file(ENV_FILE)

VALID_SERVICES = ["all", "orchestrator", "triage", "visual", "vitals"]

# Which part of the app this process runs. "all" = everything in one process (development).
SERVICE = os.environ.get("SERVICE") or "all"

if SERVICE not in VALID_SERVICES:
    # Fail at startup with a clear message instead of starting a server with no routes.
    raise ValueError(f"SERVICE must be one of {VALID_SERVICES}, got '{SERVICE}'")

# Base URL of each motor. The orchestrator adds "/motor/<name>/analyze" to these.
# A dict keyed by motor name keeps the orchestrator code free of if/else chains.
# "or" (instead of a get() default) also covers keys that exist in .env but are left empty.
MOTOR_URLS = {
    "triage": os.environ.get("TRIAGE_URL") or "http://localhost:8000",
    "visual": os.environ.get("VISUAL_URL") or "http://localhost:8000",
    "vitals": os.environ.get("VITALS_URL") or "http://localhost:8000",
}

# Max seconds the orchestrator waits for each motor before treating it as failed. Every model call of
# that motor (default model, JSON retries, escalation, fallbacks) must fit inside it. The motors run in
# parallel, so the slowest limit is the longest a user waits. The gateway timeout must stay above it.
MOTOR_TIMEOUTS = {
    # Spec: triage under 3 s end to end (p95).
    "triage": float(os.environ.get("TRIAGE_TIMEOUT_SECONDS") or "3"),
    # No spec target for image analysis. One Bedrock image call alone takes up to ~2.6 s (measured),
    # so this leaves room for a JSON retry or the Sonnet check.
    "visual": float(os.environ.get("VISUAL_TIMEOUT_SECONDS") or "8"),
    # The risk level comes from the rules; the model only writes the explanation (fixed text if it is late).
    "vitals": float(os.environ.get("VITALS_TIMEOUT_SECONDS") or "3"),
}

# A motor stops calling models this much earlier, so its answer (and the HTTP reply) reaches the
# orchestrator before its limit. Without it a slow escalation made the motor answer too late
# and a valid first answer was thrown away.
MOTOR_REPLY_MARGIN_SECONDS = 0.3


def motor_timeout_seconds(motor: str) -> float:
    """How long the orchestrator waits for `motor` (a function, so tests that change MOTOR_TIMEOUTS see it)."""
    return MOTOR_TIMEOUTS[motor]


def motor_budget_seconds(motor: str) -> float:
    """Time `motor` may spend on model calls: its limit minus the reply margin."""
    return max(MOTOR_TIMEOUTS[motor] - MOTOR_REPLY_MARGIN_SECONDS, 0.1)


def longest_motor_timeout_seconds() -> float:
    """The slowest motor's limit: the Bedrock client's read timeout must not be shorter."""
    return max(MOTOR_TIMEOUTS.values())

# ---------- Redis queue ----------

REDIS_URL = os.environ.get("REDIS_URL") or "redis://localhost:6379/0"

# A job that fails this many times is moved to the dead-letter stream instead of being retried forever.
MAX_JOB_ATTEMPTS = int(os.environ.get("MAX_JOB_ATTEMPTS") or "5")

# If Redis is down, jobs are written here (one JSON line each) and the worker replays them later.
FAILED_ENQUEUE_FILE = os.environ.get("FAILED_ENQUEUE_FILE") or "failed_enqueue.jsonl"

# Stream names. A Redis Stream is an append-only list of messages that workers read from.
STREAM_SAVE_ASSESSMENT = "jobs:save_assessment"  # one message = everything about one /ai/assess call
STREAM_AUDIT = "jobs:audit"  # one message = one standalone audit_log row
STREAM_DEAD = "jobs:dead"  # jobs that failed MAX_JOB_ATTEMPTS times, kept with their error

# Consumer group = the workers that share one stream. Each message goes to only one worker in the group.
CONSUMER_GROUP = "db-writers"

# ---------- Postgres (values come from .env, written by setup_db.sh) ----------

DB_HOST = os.environ.get("DB_HOST") or "localhost"
DB_PORT = int(os.environ.get("DB_PORT") or "5432")
DB_NAME = os.environ.get("DB_NAME", "")
DB_APP_USER = os.environ.get("DB_APP_USER", "")
DB_APP_PASSWORD = os.environ.get("DB_APP_PASSWORD", "")
<<<<<<< Updated upstream
=======

# ---------- AI models (Amazon Bedrock) ----------

# "false" (default) = the motors return their stub answers and nothing calls AWS.
# Keep it false wherever real patient data could flow until the AWS BAA is signed.
USE_BEDROCK = (os.environ.get("USE_BEDROCK") or "false").strip().lower() in ("1", "true", "yes")

# Bedrock region. The model IDs in app/configurations/models.py are US cross-region profiles.
# Credentials come from the standard AWS chain (the EC2 instance's IAM role), never from .env.
AWS_REGION = os.environ.get("AWS_REGION") or "us-west-2"

# Largest image (after base64 decoding) Bedrock accepts in one request: 3.75 MB.
MAX_IMAGE_BYTES = int(3.75 * 1024 * 1024)

# ---------- Input checks for POST /ai/assess ----------

# Longest symptoms text accepted (after cleaning). Longer = 422.
MAX_SYMPTOMS_CHARS = int(os.environ.get("MAX_SYMPTOMS_CHARS") or "2000")

# "true" (default) = /ai/assess refuses users who have not given the consents in app/configurations/consent.py.
# Set it to false only for local development and tests; main.py logs a warning at startup when it is off.
CONSENT_CHECK_ENABLED = (os.environ.get("CONSENT_CHECK_ENABLED") or "true").strip().lower() in ("1", "true", "yes")

# ---------- Trusted medical source library (app/medical_sources/library.py) ----------

# Statuses of ai.medical_sources the AI may pick from. Production: "active" only (client-approved sources).
# For a demo before the client's written approval, "active,draft,proposed_addition" also gives the draft list.
MEDICAL_SOURCE_STATUSES = tuple(
    status.strip() for status in (os.environ.get("MEDICAL_SOURCE_STATUSES") or "active").split(",") if status.strip()
)

# How long a motor keeps the library in memory before reading ai.medical_sources again.
MEDICAL_SOURCES_CACHE_SECONDS = float(os.environ.get("MEDICAL_SOURCES_CACHE_SECONDS") or "300")

# ---------- Daily link check of the medical sources (app/cron_jobs/source_check/check_links.py) ----------

# Who gets one email per run listing the sources set to "broken" (comma-separated). Empty = no email, only the
# WARNING in the log. Sent through AWS SES in AWS_REGION; LINK_ALERT_FROM must be an SES-verified address.
LINK_ALERT_EMAILS = tuple(
    address.strip() for address in (os.environ.get("LINK_ALERT_EMAILS") or "").split(",") if address.strip()
)
LINK_ALERT_FROM = (os.environ.get("LINK_ALERT_FROM") or "").strip()
>>>>>>> Stashed changes
