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
        values[key.strip()] = value.strip()  # a key listed twice: the last one wins, same as "source .env"
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

# Max seconds to wait for one motor before treating it as failed.
MOTOR_TIMEOUT_SECONDS = float(os.environ.get("MOTOR_TIMEOUT_SECONDS") or "2.5")

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

# ---------- AI models (Amazon Bedrock) ----------

# "false" (default) = the motors return their stub answers and nothing calls AWS.
# Keep it false wherever real patient data could flow until the AWS BAA is signed.
USE_BEDROCK = (os.environ.get("USE_BEDROCK") or "false").strip().lower() in ("1", "true", "yes")

# Bedrock region. The model IDs in app/configurations/models.py are US cross-region profiles.
# Credentials come from the standard AWS chain (the EC2 instance's IAM role), never from .env.
AWS_REGION = os.environ.get("AWS_REGION") or "us-west-2"
