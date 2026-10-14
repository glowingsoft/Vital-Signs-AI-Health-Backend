#!/usr/bin/env bash
# One-step local database setup for the AI module (Mac).
# 1. Creates db/sql/01_roles_and_database.sql and db/sql/02_schema_and_tables.sql
# 2. Adds the DB_* settings to .env if missing (passwords are generated, not hard-coded)
# 3. Creates the logins, the database, the "ai" schema and the 8 tables
# Safe to run again: existing .env values are kept and existing database objects are skipped.
#
# Run from the project folder:   bash setup_db.sh
# Delete the ai schema and start fresh (LOCAL ONLY, deletes all ai data):
#                                RESET_AI_SCHEMA=yes bash setup_db.sh

set -euo pipefail
cd "$(dirname "$0")"

command -v psql >/dev/null || { echo "ERROR: psql not found. Run: brew install postgresql@16"; exit 1; }

# ---------------------------------------------------------------------------
# 1. Create the SQL files
# ---------------------------------------------------------------------------
mkdir -p db/sql
echo "Writing db/sql/01_roles_and_database.sql"
cat > db/sql/01_roles_and_database.sql <<'SQL_EOF'
-- Step 1 of 2. Run as the ADMIN user, connected to the "postgres" database.
-- Creates the logins (AI module + Node) and the "vitalsigns" database.
-- Safe to run many times: anything that already exists is skipped.
-- Requires PostgreSQL 16 (local and AWS RDS).

\set ON_ERROR_STOP on

-- AI module login: create it, or update its password if it already exists.
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'app_user', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user') \gexec

SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'app_user', :'app_password')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user') \gexec

-- Node backend login (only when a Node user was given).
\if :has_node
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'node_user', :'node_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'node_user') \gexec

SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'node_user', :'node_password')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'node_user') \gexec
\endif

-- On AWS RDS the admin is not a full superuser. To create a database owned by the
-- AI login, the admin must be allowed to act as that login. Locally this is skipped.
SELECT format('GRANT %I TO %I WITH SET TRUE', :'app_user', current_user)
WHERE NOT pg_has_role(current_user, :'app_user', 'SET') \gexec

-- The database, owned by the AI login.
SELECT format('CREATE DATABASE %I OWNER %I ENCODING ''UTF8''', :'app_db', :'app_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'app_db') \gexec
SQL_EOF

echo "Writing db/sql/02_schema_and_tables.sql"
cat > db/sql/02_schema_and_tables.sql <<'SQL_EOF'
-- Step 2 of 2. Run as the AI login (vs_ai), connected to the "vitalsigns" database.
-- Creates the "ai" schema, the 8 tables, indexes, permissions and the
-- append-only protection. Safe to run many times.
--
-- Sources in the COMMENT lines:
--   Proposal = "Vital Signs Phase 3 Project Proposal (Updated)"
--   Spec     = "Phase Three - Developer Requirements Spec"
--
-- Rules for every table:
--   * user_id is a pseudonymous ID. Never store name, email, phone or address (Spec, Privacy).
--   * Rows are never edited: a change is a new row in ai.reviews, and every change
--     is also written to ai.audit_log with before/after values.
--   * Keep data for at least 7 years (Spec, Data collection).

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------------
-- Access to the database and schema
-- ---------------------------------------------------------------------------
SELECT format('REVOKE CONNECT, TEMPORARY ON DATABASE %I FROM PUBLIC', current_database()) \gexec

CREATE SCHEMA IF NOT EXISTS ai;
COMMENT ON SCHEMA ai IS 'AI module tables. Structure changed only by this script / Alembic. Node reads; inserts only where granted.';

REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- Append-only protection: blocks UPDATE, DELETE and TRUNCATE, even for the owner.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION ai.block_changes() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Table %.% is append-only: % is not allowed. Insert a new row instead.',
        TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP;
END;
$$;

-- ---------------------------------------------------------------------------
-- 1. disclaimers: versioned warning texts
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai.disclaimers (
    id           text        PRIMARY KEY,
    version      integer     NOT NULL,
    language     text        NOT NULL DEFAULT 'en',
    text         text        NOT NULL,
    active_from  timestamptz NOT NULL DEFAULT now(),
    active_to    timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE  ai.disclaimers IS 'Versioned disclaimer texts. Each result stores the disclaimer_id shown (Spec, FDA language layer).';
COMMENT ON COLUMN ai.disclaimers.active_to IS 'Empty = the current version.';

INSERT INTO ai.disclaimers (id, version, language, text)
VALUES ('disc_v1', 1, 'en', 'This result is not a diagnosis. Always consult your doctor.')
ON CONFLICT (id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 2. vitals_readings: the health numbers, with their first label
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai.vitals_readings (
    id                 uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id            text         NOT NULL,
    client_reading_id  text         NOT NULL,
    measured_at        timestamptz  NOT NULL,
    received_at        timestamptz  NOT NULL DEFAULT now(),
    heart_rate         integer      CHECK (heart_rate BETWEEN 0 AND 300),
    systolic_bp        integer      CHECK (systolic_bp BETWEEN 0 AND 300),
    diastolic_bp       integer      CHECK (diastolic_bp BETWEEN 0 AND 250),
    respiratory_rate   integer      CHECK (respiratory_rate BETWEEN 0 AND 100),
    spo2               numeric(5,2) CHECK (spo2 BETWEEN 0 AND 100),
    hrv_ms             numeric(7,2) CHECK (hrv_ms >= 0),
    sleep_minutes      integer      CHECK (sleep_minutes >= 0),
    label              text         NOT NULL DEFAULT 'normal'
                                    CHECK (label IN ('confirmed_diagnosis', 'reported_symptom', 'normal')),
    label_source       text         NOT NULL DEFAULT 'user'
                                    CHECK (label_source IN ('user', 'labeler', 'supervisor', 'doctor', 'system')),
    model_version      text,
    source             text         NOT NULL CHECK (source IN ('apple_health', 'samsung_health', 'device', 'manual')),
    recorded_offline   boolean      NOT NULL DEFAULT false,
    device_platform    text         CHECK (device_platform IN ('ios', 'android')),
    device_model       text,
    os_version         text,
    app_version        text,
    state              text,
    country            text         NOT NULL,
    created_at         timestamptz  NOT NULL DEFAULT now(),
    UNIQUE (user_id, client_reading_id),
    CHECK (num_nonnulls(heart_rate, systolic_bp, diastolic_bp, respiratory_rate, spo2, hrv_ms, sleep_minutes) > 0)
);
COMMENT ON TABLE  ai.vitals_readings IS 'Every vitals reading with its label, label source, model version, state, country and device info (Spec, Record schema; Proposal M1).';
COMMENT ON COLUMN ai.vitals_readings.user_id IS 'Pseudonymous ID only (Spec, Privacy).';
COMMENT ON COLUMN ai.vitals_readings.client_reading_id IS 'ID created on the phone. Unique per user, so offline sync never saves a reading twice (Spec, Motor 3 offline).';
COMMENT ON COLUMN ai.vitals_readings.label IS 'First label when the reading arrives. Later labels are rows in ai.reviews (Spec, The three mandatory labels).';
COMMENT ON COLUMN ai.vitals_readings.label_source IS 'Who set the first label (Spec, Record schema).';
COMMENT ON COLUMN ai.vitals_readings.model_version IS 'Version of the on-device vitals model that analysed it (Spec, Record schema).';
COMMENT ON COLUMN ai.vitals_readings.recorded_offline IS 'True when captured with no internet and synced later (Proposal Week 2).';

CREATE INDEX IF NOT EXISTS vitals_readings_user_time_idx ON ai.vitals_readings (user_id, measured_at DESC);

-- ---------------------------------------------------------------------------
-- 3. assessments: the AI response the user saw (append-only)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai.assessments (
    id                uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           text         NOT NULL,
    reading_id        uuid         REFERENCES ai.vitals_readings (id),
    input_types       text[]       NOT NULL,
    input             jsonb        NOT NULL,
    risk_level        text         NOT NULL CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
    confidence_score  numeric(4,3) NOT NULL CHECK (confidence_score BETWEEN 0 AND 1),
    explanation       text         NOT NULL,
    label             text         NOT NULL
                                   CHECK (label IN ('confirmed_diagnosis', 'reported_symptom', 'normal')),
    label_source      text         NOT NULL DEFAULT 'system'
                                   CHECK (label_source IN ('user', 'labeler', 'supervisor', 'doctor', 'system')),
    disclaimer_id     text         NOT NULL REFERENCES ai.disclaimers (id),
    motors_used       text[]       NOT NULL DEFAULT '{}',
    motors_failed     text[]       NOT NULL DEFAULT '{}',
    total_latency_ms  integer      CHECK (total_latency_ms >= 0),
    app_version       text,
    state             text,
    country           text,
    created_at        timestamptz  NOT NULL DEFAULT now()
);
COMMENT ON TABLE  ai.assessments IS 'One row per /ai/assess call: the final answer the user saw. Together with user rows in ai.reviews this is the prediction audit log: date, input, disclaimer shown, user response (Spec, FDA language layer; Proposal Week 3). Append-only.';
COMMENT ON COLUMN ai.assessments.id IS 'The prediction_id returned to the app.';
COMMENT ON COLUMN ai.assessments.input IS 'What was sent to the AI (symptoms text, vitals). Never image bytes: images are stored in encrypted S3 and referenced by key.';
COMMENT ON COLUMN ai.assessments.explanation IS 'Exactly as shown, after the FDA language filter (Spec, FDA language layer).';
COMMENT ON COLUMN ai.assessments.label IS 'Label included in the AI response. Changes are rows in ai.reviews.';
COMMENT ON COLUMN ai.assessments.total_latency_ms IS 'For the < 3 s triage target (Spec, Latency targets).';

CREATE INDEX IF NOT EXISTS assessments_user_time_idx ON ai.assessments (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS assessments_reading_idx   ON ai.assessments (reading_id);

CREATE OR REPLACE TRIGGER assessments_no_change
    BEFORE UPDATE OR DELETE ON ai.assessments
    FOR EACH ROW EXECUTE FUNCTION ai.block_changes();
CREATE OR REPLACE TRIGGER assessments_no_truncate
    BEFORE TRUNCATE ON ai.assessments
    FOR EACH STATEMENT EXECUTE FUNCTION ai.block_changes();

-- ---------------------------------------------------------------------------
-- 4. motor_results: each motor's own answer
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai.motor_results (
    id                uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    assessment_id     uuid         NOT NULL REFERENCES ai.assessments (id),
    motor             text         NOT NULL CHECK (motor IN ('triage', 'visual', 'vitals')),
    risk_level        text         NOT NULL CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
    confidence_score  numeric(4,3) NOT NULL CHECK (confidence_score BETWEEN 0 AND 1),
    explanation       text         NOT NULL,
    finding_type      text,
    evidence_level    text,
    reference         text,
    model_name        text         NOT NULL,
    model_version     text,
    escalated         boolean      NOT NULL DEFAULT false,
    fallback_used     boolean      NOT NULL DEFAULT false,
    latency_ms        integer      CHECK (latency_ms >= 0),
    created_at        timestamptz  NOT NULL DEFAULT now()
);
COMMENT ON TABLE  ai.motor_results IS 'Each motor''s own answer inside one assessment, before consolidation.';
COMMENT ON COLUMN ai.motor_results.explanation IS 'Raw motor text, before the FDA filter. Kept for audit.';
COMMENT ON COLUMN ai.motor_results.finding_type IS 'Medical-source field (Spec, Addendum A).';
COMMENT ON COLUMN ai.motor_results.evidence_level IS 'Medical-source field (Spec, Addendum A).';
COMMENT ON COLUMN ai.motor_results.reference IS 'Traceable reference from the curated library (Spec, Addendum A).';
COMMENT ON COLUMN ai.motor_results.model_name IS 'Model stored per response (Spec, Motor 1). "stub" during M1.';
COMMENT ON COLUMN ai.motor_results.escalated IS 'True when Sonnet was used after Haiku (Proposal, Routing strategy).';
COMMENT ON COLUMN ai.motor_results.fallback_used IS 'True when a backup model was used (Proposal, Routing strategy).';
COMMENT ON COLUMN ai.motor_results.latency_ms IS 'For the per-model p95 latency benchmark (Spec, Latency targets).';

CREATE INDEX IF NOT EXISTS motor_results_assessment_idx ON ai.motor_results (assessment_id);

-- ---------------------------------------------------------------------------
-- 5. ai_usage: cost of every AI call
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai.ai_usage (
    id                uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    assessment_id     uuid          REFERENCES ai.assessments (id),
    user_id           text          NOT NULL,
    motor             text          NOT NULL CHECK (motor IN ('triage', 'visual', 'vitals')),
    model_name        text          NOT NULL,
    aws_region        text,
    input_tokens      integer       NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    output_tokens     integer       NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    reasoning_tokens  integer       NOT NULL DEFAULT 0 CHECK (reasoning_tokens >= 0),
    image_count       integer       NOT NULL DEFAULT 0 CHECK (image_count >= 0),
    is_escalation     boolean       NOT NULL DEFAULT false,
    is_fallback       boolean       NOT NULL DEFAULT false,
    is_retry          boolean       NOT NULL DEFAULT false,
    cost_usd          numeric(12,6) NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),
    latency_ms        integer       CHECK (latency_ms >= 0),
    created_at        timestamptz   NOT NULL DEFAULT now()
);
COMMENT ON TABLE  ai.ai_usage IS 'One row per billable AI call: cost metering by user, model and motor for the USD 0.50 target (Proposal M1 and AI cost section).';
COMMENT ON COLUMN ai.ai_usage.reasoning_tokens IS 'Billable reasoning tokens count toward the target (Proposal, How the target is measured).';
COMMENT ON COLUMN ai.ai_usage.is_retry IS 'Retries and failovers count toward the target (Proposal, How the target is measured).';

CREATE INDEX IF NOT EXISTS ai_usage_user_time_idx  ON ai.ai_usage (user_id, created_at);
CREATE INDEX IF NOT EXISTS ai_usage_time_idx       ON ai.ai_usage (created_at);
CREATE INDEX IF NOT EXISTS ai_usage_assessment_idx ON ai.ai_usage (assessment_id);

-- ---------------------------------------------------------------------------
-- 6. reviews: user responses, doctor reviews and label changes (append-only)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai.reviews (
    id             uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    assessment_id  uuid        REFERENCES ai.assessments (id),
    reading_id     uuid        REFERENCES ai.vitals_readings (id),
    reviewer_id    text        NOT NULL,
    reviewer_role  text        NOT NULL CHECK (reviewer_role IN ('user', 'doctor', 'labeler', 'supervisor')),
    action         text        NOT NULL CHECK (action IN ('responded', 'confirmed', 'corrected', 'labeled')),
    label          text        CHECK (label IN ('confirmed_diagnosis', 'reported_symptom', 'normal')),
    risk_level     text        CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
    user_response  text,
    note           text,
    is_final       boolean     NOT NULL DEFAULT false,
    created_at     timestamptz NOT NULL DEFAULT now(),
    CHECK (assessment_id IS NOT NULL OR reading_id IS NOT NULL),
    CHECK (action <> 'responded' OR (reviewer_role = 'user' AND user_response IS NOT NULL)),
    CHECK (action <> 'corrected' OR (label IS NOT NULL OR risk_level IS NOT NULL)),
    CHECK (action <> 'labeled'   OR label IS NOT NULL)
);
COMMENT ON TABLE  ai.reviews IS 'Every human review of an AI result or reading, newest row wins: user responses (Spec, prediction audit "user response"), doctor confirmations and corrections (Spec, Motor 1 and Addendum A), and label changes by labelers and supervisors (Spec, Labeling workflow). Append-only.';
COMMENT ON COLUMN ai.reviews.action IS 'responded = user reacted to the result; confirmed = doctor agrees; corrected = new risk level and/or label; labeled = labeler/supervisor sets the label.';
COMMENT ON COLUMN ai.reviews.user_response IS 'e.g. acknowledged, contacted_doctor, dismissed, disagreed.';
COMMENT ON COLUMN ai.reviews.is_final IS 'True = state clinical supervisor''s final decision (Spec, Labeling workflow).';

CREATE INDEX IF NOT EXISTS reviews_assessment_idx ON ai.reviews (assessment_id, created_at DESC);
CREATE INDEX IF NOT EXISTS reviews_reading_idx    ON ai.reviews (reading_id, created_at DESC);

CREATE OR REPLACE TRIGGER reviews_no_change
    BEFORE UPDATE OR DELETE ON ai.reviews
    FOR EACH ROW EXECUTE FUNCTION ai.block_changes();
CREATE OR REPLACE TRIGGER reviews_no_truncate
    BEFORE TRUNCATE ON ai.reviews
    FOR EACH STATEMENT EXECUTE FUNCTION ai.block_changes();

-- ---------------------------------------------------------------------------
-- 7. audit_log: every view and change, with before/after (append-only)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai.audit_log (
    id             uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_id       text        NOT NULL,
    actor_role     text        NOT NULL CHECK (actor_role IN
                       ('patient', 'doctor', 'supervisor', 'labeler', 'admin', 'developer', 'system')),
    action         text        NOT NULL CHECK (action IN ('view', 'create', 'update', 'export', 'delete')),
    resource_type  text        NOT NULL,
    resource_id    text,
    patient_id     text,
    reason         text        NOT NULL CHECK (length(trim(reason)) > 0),
    before         jsonb,
    after          jsonb,
    service        text        NOT NULL CHECK (service IN ('ai', 'node')),
    ip_address     inet,
    created_at     timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE  ai.audit_log IS 'Who viewed or changed health data, when and why (Proposal M1 §13; Spec, Privacy), and the clinical audit trail with before/after values, e.g. a doctor changing the label or risk of an AI response (Spec, Addendum A). Append-only.';
COMMENT ON COLUMN ai.audit_log.reason IS 'Why the data was accessed or changed. Required (Spec, Mandatory controls).';
COMMENT ON COLUMN ai.audit_log.before IS 'Values before the change, e.g. {"label": "reported_symptom", "risk_level": "medium"}.';
COMMENT ON COLUMN ai.audit_log.after IS 'Values after the change, e.g. {"label": "confirmed_diagnosis", "risk_level": "high"}.';

CREATE INDEX IF NOT EXISTS audit_log_patient_time_idx  ON ai.audit_log (patient_id, created_at DESC);
CREATE INDEX IF NOT EXISTS audit_log_actor_time_idx    ON ai.audit_log (actor_id, created_at DESC);
CREATE INDEX IF NOT EXISTS audit_log_resource_idx      ON ai.audit_log (resource_type, resource_id);

CREATE OR REPLACE TRIGGER audit_log_no_change
    BEFORE UPDATE OR DELETE ON ai.audit_log
    FOR EACH ROW EXECUTE FUNCTION ai.block_changes();
CREATE OR REPLACE TRIGGER audit_log_no_truncate
    BEFORE TRUNCATE ON ai.audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION ai.block_changes();

-- ---------------------------------------------------------------------------
-- 8. consents: what the user agreed to (append-only)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai.consents (
    id                  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             text        NOT NULL,
    consent_type        text        NOT NULL CHECK (consent_type IN
                            ('monitoring', 'research', 'voice_recording', 'camera_image', 'mexico_data_transfer')),
    granted             boolean     NOT NULL,
    disclaimer_version  text        NOT NULL,
    app_version         text        NOT NULL,
    ip_address          inet        NOT NULL,
    state               text,
    country             text,
    created_at          timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE  ai.consents IS 'Every consent with timestamp, IP, app version and disclaimer version (Proposal §14; Spec, Consent and user rights). Withdrawal = new row with granted = false, stored with its timestamp. Append-only. Written by Node.';
COMMENT ON COLUMN ai.consents.consent_type IS 'monitoring (required before monitoring starts), research (optional, revocable), voice_recording (Spec, Addendum A), camera_image (Spec, Motor 2), mexico_data_transfer (Proposal Week 4).';
COMMENT ON COLUMN ai.consents.granted IS 'true = given, false = refused or withdrawn. The newest row per user and type is the current state.';

CREATE INDEX IF NOT EXISTS consents_user_type_time_idx ON ai.consents (user_id, consent_type, created_at DESC);

CREATE OR REPLACE TRIGGER consents_no_change
    BEFORE UPDATE OR DELETE ON ai.consents
    FOR EACH ROW EXECUTE FUNCTION ai.block_changes();
CREATE OR REPLACE TRIGGER consents_no_truncate
    BEFORE TRUNCATE ON ai.consents
    FOR EACH STATEMENT EXECUTE FUNCTION ai.block_changes();

-- ---------------------------------------------------------------------------
-- Node backend permissions
-- Reads every ai table. Inserts only into reviews, audit_log and consents.
-- No UPDATE, DELETE or structure changes.
-- ---------------------------------------------------------------------------
\if :has_node
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'node_user') \gexec
GRANT USAGE  ON SCHEMA ai TO :"node_user";
GRANT SELECT ON ALL TABLES IN SCHEMA ai TO :"node_user";
GRANT INSERT ON ai.reviews, ai.audit_log, ai.consents TO :"node_user";
ALTER DEFAULT PRIVILEGES IN SCHEMA ai GRANT SELECT ON TABLES TO :"node_user";
\endif
SQL_EOF

# ---------------------------------------------------------------------------
# 2. Database settings in .env (added only if missing; your values are kept)
# ---------------------------------------------------------------------------
touch .env .env.example

add_setting() {
  # $1 = key, $2 = default value for .env
  grep -q "^$1=" .env         || echo "$1=$2" >> .env
  grep -q "^$1=" .env.example || echo "$1=" >> .env.example
}
new_password() { openssl rand -hex 16; }

add_setting DB_HOST          localhost
add_setting DB_PORT          5432
add_setting DB_NAME          vitalsigns
add_setting DB_APP_USER      vs_ai
add_setting DB_APP_PASSWORD  "$(new_password)"
add_setting DB_NODE_USER     vs_node
add_setting DB_NODE_PASSWORD "$(new_password)"

grep -qx ".env" .gitignore 2>/dev/null || echo ".env" >> .gitignore

set -a; source .env; set +a

for key in DB_HOST DB_PORT DB_NAME DB_APP_USER DB_APP_PASSWORD DB_NODE_USER DB_NODE_PASSWORD; do
  [[ -n "${!key:-}" ]] || { echo "ERROR: $key is empty in .env. Fill it in and run again."; exit 1; }
done

# ---------------------------------------------------------------------------
# 3. Create logins, database, schema and tables
# ---------------------------------------------------------------------------
PSQL=(psql -X -q -v ON_ERROR_STOP=1 -h "$DB_HOST" -p "$DB_PORT")

echo "Creating logins and database ..."
"${PSQL[@]}" -d postgres \
  -v app_db="$DB_NAME" \
  -v app_user="$DB_APP_USER" -v app_password="$DB_APP_PASSWORD" \
  -v node_user="$DB_NODE_USER" -v node_password="$DB_NODE_PASSWORD" \
  -v has_node=true \
  -f db/sql/01_roles_and_database.sql

if [[ "${RESET_AI_SCHEMA:-}" == "yes" ]]; then
  if [[ "$DB_HOST" != "localhost" && "$DB_HOST" != "127.0.0.1" ]]; then
    echo "ERROR: RESET_AI_SCHEMA only works on localhost. Refusing to delete data on $DB_HOST."
    exit 1
  fi
  echo "Deleting the ai schema and all its tables (local reset) ..."
  PGPASSWORD="$DB_APP_PASSWORD" "${PSQL[@]}" -U "$DB_APP_USER" -d "$DB_NAME" \
    -c 'DROP SCHEMA IF EXISTS ai CASCADE'
fi

echo "Creating schema and tables ..."
PGPASSWORD="$DB_APP_PASSWORD" "${PSQL[@]}" -U "$DB_APP_USER" -d "$DB_NAME" \
  -v node_user="$DB_NODE_USER" -v has_node=true \
  -f db/sql/02_schema_and_tables.sql

echo
PGPASSWORD="$DB_APP_PASSWORD" "${PSQL[@]}" -U "$DB_APP_USER" -d "$DB_NAME" -c '\dt ai.*'

echo "Done. To view in TablePlus use the DB_* values in .env:"
echo "  Host $DB_HOST  Port $DB_PORT  User $DB_APP_USER  Database $DB_NAME  (password: DB_APP_PASSWORD in .env)"
