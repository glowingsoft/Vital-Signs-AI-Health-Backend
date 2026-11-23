"""Milestone 1 check: tests every M1 deliverable against the RUNNING AI service and prints PASS / FAIL.

It makes real requests through the gateway, then looks in Postgres and Redis to see that they were saved.

On the server (every check, including the database and the queue). Run from the app folder:
    cd /opt/vitalsigns-ai
    sudo docker compose -f docker-compose.yml -f docker-compose.aws.yml exec orchestrator python -m scripts.verify_m1

On your Mac against local Docker (gateway on http://localhost, no HTTPS there):
    python -m scripts.verify_m1 --base-url http://localhost --skip-tls

On your Mac against the server, without the database and queue checks (needs the server's JWT_SECRET):
    JWT_SECRET=... python -m scripts.verify_m1 --skip-db

Exit code 0 = no FAIL (WARN and SKIP are allowed), 1 = at least one FAIL.

Test data: every run saves 4 assessments for the test patient VERIFY_USER_ID (below). The tables are
append-only, so these rows stay. Filter them out of reports with user_id <> VERIFY_USER_ID.
"""

import argparse
import asyncio
import os
import socket
import ssl
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

import httpx
import jwt

from app import config  # importing it loads .env into os.environ (JWT_SECRET, DB_*, REDIS_URL)
from python_auth.roles import UserType

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE_URL = "https://ai.dev.vitals-signs.com"

# Fake patient used only by this script (24 hex characters, like a MongoDB id).
VERIFY_USER_ID = "0000000000000000000000f1"

TABLES = ["vitals_readings", "assessments", "motor_results", "ai_usage", "reviews", "audit_log", "consents", "disclaimers"]
APPEND_ONLY_TABLES = ["assessments", "reviews", "audit_log", "consents"]
LABELS = ["confirmed_diagnosis", "reported_symptom", "normal"]
RISK_LEVELS = ["low", "medium", "high", "critical"]


# ---------------------------------------------------------------- output

class Report:
    def __init__(self) -> None:
        self.counts = {"PASS": 0, "FAIL": 0, "WARN": 0, "SKIP": 0}
        self.failures: List[str] = []

    def section(self, title: str) -> None:
        print(f"\n=== {title} ===")

    def add(self, status: str, name: str, detail: str = "") -> None:
        self.counts[status] += 1
        icons = {"PASS": "PASS", "FAIL": "FAIL", "WARN": "WARN", "SKIP": "SKIP"}
        line = f"  [{icons[status]}] {name}"
        if detail:
            line += f"  ({detail})"
        print(line)
        if status == "FAIL":
            self.failures.append(name)

    def check(self, ok: bool, name: str, detail: str = "") -> bool:
        self.add("PASS" if ok else "FAIL", name, detail)
        return ok

    def summary(self) -> int:
        c = self.counts
        print(f"\n=== Result: {c['PASS']} passed, {c['FAIL']} failed, {c['WARN']} warnings, {c['SKIP']} skipped ===")
        if self.failures:
            print("Failed:")
            for name in self.failures:
                print(f"  - {name}")
            return 1
        print("Milestone 1: all checks passed.")
        return 0


# ---------------------------------------------------------------- tokens

def make_token(user_type: UserType, is_refresh: bool = False) -> str:
    """A token shaped like the NestJS ones (python_auth/auth.py), signed with this server's JWT_SECRET."""
    now = int(time.time())
    payload = {
        "_id": VERIFY_USER_ID,
        "user_type": int(user_type),
        "is_refresh": is_refresh,
        "iat": now,
        "exp": now + 600,
    }
    return jwt.encode(payload, os.environ["JWT_SECRET"], algorithm="HS256")


async def post_assess(client: httpx.AsyncClient, body: dict, token: Optional[str]) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = await client.post("/ai/assess", json=body, headers=headers)
    if response.status_code == 429:  # the gateway rate limit; wait and try once more
        await asyncio.sleep(1.5)
        response = await client.post("/ai/assess", json=body, headers=headers)
    return response


# ---------------------------------------------------------------- 1. gateway

async def check_gateway(report: Report, client: httpx.AsyncClient) -> bool:
    """Returns False when the gateway cannot be reached at all (the other checks are then pointless)."""
    report.section("1. API gateway and orchestrator")

    try:
        response = await client.get("/health")
    except Exception as error:
        report.add("FAIL", "Gateway answers", f"{error!r}")
        return False
    body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    report.check(response.status_code == 200 and body.get("status") == "ok", "GET /health answers 200 through the gateway", f"HTTP {response.status_code}")
    report.check(body.get("service") in ("orchestrator", "all"), "Gateway forwards to the orchestrator", f"service={body.get('service')}")
    report.check(body.get("redis") == "ok", "Orchestrator reaches Redis (message queue)", f"redis={body.get('redis')}")
    report.check(body.get("database") == "ok", "Orchestrator reaches Postgres", f"database={body.get('database')}")

    for method, path in [("POST", "/motor/triage/analyze"), ("POST", "/motor/visual/analyze"), ("POST", "/motor/vitals/analyze")]:
        response = await client.request(method, path, json={})
        report.check(response.status_code == 404, f"Motor endpoint {path} is hidden from outside", f"HTTP {response.status_code}")
    for path in ["/docs", "/openapi.json"]:
        response = await client.get(path)
        report.check(response.status_code == 404, f"{path} is not public", f"HTTP {response.status_code}")

    server = response.headers.get("server", "")
    report.check(server in ("", "nginx"), "Gateway hides the nginx version", f"Server: {server or '(none)'}")
    return True


async def check_rate_limit(report: Report, client: httpx.AsyncClient) -> None:
    report.section("8. Gateway rate limit")
    responses = await asyncio.gather(*(client.get("/health") for _ in range(60)), return_exceptions=True)
    codes = [r.status_code for r in responses if isinstance(r, httpx.Response)]
    limited = codes.count(429)
    report.check(limited > 0, "Gateway rate limit answers 429 when one IP sends too fast", f"{limited} of 60 burst requests limited")


# ---------------------------------------------------------------- 2. REST + contract

def check_contract(report: Report, name: str, response: httpx.Response, expected_motors: List[str]) -> Optional[str]:
    """Checks one /ai/assess answer against the data contract. Returns the prediction_id."""
    if not report.check(response.status_code == 200, f"{name}: HTTP 200", f"HTTP {response.status_code} {response.text[:150]}"):
        return None
    body = response.json()
    problems = []
    if body.get("risk_level") not in RISK_LEVELS:
        problems.append(f"risk_level={body.get('risk_level')}")
    confidence = body.get("confidence_score")
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        problems.append(f"confidence_score={confidence}")
    if not isinstance(body.get("explanation"), str) or not body["explanation"].strip():
        problems.append("explanation empty")
    if not body.get("disclaimer_id"):
        problems.append("disclaimer_id missing")
    if not body.get("disclaimer"):
        problems.append("disclaimer text missing")
    try:
        uuid.UUID(str(body.get("prediction_id")))
    except ValueError:
        problems.append("prediction_id is not a UUID")
    report.check(not problems, f"{name}: contract fields (risk level, confidence, explanation, disclaimer ID)", ", ".join(problems))
    report.check(
        sorted(body.get("motors_used", [])) == sorted(expected_motors) and body.get("motors_failed") == [],
        f"{name}: motors called",
        f"used={body.get('motors_used')} failed={body.get('motors_failed')}",
    )
    return body.get("prediction_id")


async def check_rest(report: Report, client: httpx.AsyncClient, token: str) -> dict:
    """Calls each motor through /ai/assess. Returns {name: prediction_id} for the database checks."""
    report.section("2. REST endpoints and JSON data contract")
    context = {"app_version": "verify-m1", "state": "CA", "country": "US", "device_platform": "ios", "device_model": "verify-m1", "os_version": "18.0"}
    run = uuid.uuid4().hex[:8]
    cases = {
        "triage": ({"symptoms": "mild headache since this morning", "context": context}, ["triage"]),
        "vitals": (
            {"vitals": {"heart_rate": 82, "spo2": 97, "systolic": 122, "diastolic": 80, "respiratory_rate": 15,
                        "client_reading_id": f"verify-m1-{run}"}, "context": context},
            ["vitals"],
        ),
        "visual": ({"image": f"verify-m1-image-{run}", "context": context}, ["visual"]),
        "all motors": (
            {"symptoms": "chest pain and dizziness", "image": f"verify-m1-image-{run}-2",
             "vitals": {"heart_rate": 118, "spo2": 94, "client_reading_id": f"verify-m1-{run}-2"}, "context": context},
            ["triage", "visual", "vitals"],
        ),
    }
    predictions = {}
    for name, (body, motors) in cases.items():
        started = time.monotonic()
        response = await post_assess(client, body, token)
        took = int((time.monotonic() - started) * 1000)
        prediction_id = check_contract(report, f"POST /ai/assess ({name})", response, motors)
        if prediction_id:
            predictions[name] = prediction_id
            print(f"         prediction_id={prediction_id}  time={took} ms")
        if name == "all motors" and response.status_code == 200:
            level = response.json().get("risk_level")
            report.check(level in ("high", "critical"), "Highest risk wins when several motors answer", f"risk_level={level}")

    response = await post_assess(client, {"context": context}, token)
    report.check(response.status_code == 400, "Request with no symptoms, image or vitals is rejected", f"HTTP {response.status_code}")
    response = await post_assess(client, {"vitals": {"heart_rate": 80, "client_reading_id": "x"}}, token)
    report.check(response.status_code == 422, "Vitals without country are rejected (validation)", f"HTTP {response.status_code}")
    return predictions


# ---------------------------------------------------------------- 3 + 4. queue, worker, schema

async def check_queue(report: Report) -> None:
    report.section("4. Message queue and worker (Redis)")
    from redis.asyncio import Redis

    redis = Redis.from_url(config.REDIS_URL, decode_responses=True, socket_timeout=3)
    try:
        for stream in [config.STREAM_SAVE_ASSESSMENT, config.STREAM_AUDIT]:
            try:
                groups = await redis.xinfo_groups(stream)
            except Exception as error:
                report.add("FAIL", f"Stream {stream} has the worker group", f"{error!r}")
                continue
            group = next((g for g in groups if g["name"] == config.CONSUMER_GROUP), None)
            report.check(group is not None, f"Stream {stream} has the worker group '{config.CONSUMER_GROUP}'")
            if group is not None and stream == config.STREAM_SAVE_ASSESSMENT:
                # Section 5b proves the worker really saves; this shows it is connected and keeping up.
                report.check(group.get("consumers", 0) > 0, f"A worker is reading {stream}", f"{group.get('consumers', 0)} worker(s), {group.get('pending', 0)} pending")
        dead = await redis.xlen(config.STREAM_DEAD)
        report.add("PASS" if dead == 0 else "WARN", "No jobs in the dead-letter stream", f"{dead} job(s) in {config.STREAM_DEAD}")
    finally:
        await redis.aclose()

    fallback = Path(config.FAILED_ENQUEUE_FILE)
    if fallback.exists() and fallback.stat().st_size > 0:
        report.add("WARN", "No jobs waiting in the Redis-down fallback file", f"{fallback} is not empty")
    else:
        report.add("PASS", "No jobs waiting in the Redis-down fallback file")


async def wait_for_rows(connection, sql: str, params: dict, minimum: int, seconds: float = 20) -> int:
    from sqlalchemy import text

    deadline = time.monotonic() + seconds
    while True:
        count = (await connection.execute(text(sql), params)).scalar_one()
        if count >= minimum or time.monotonic() > deadline:
            return count
        await asyncio.sleep(0.5)


async def check_database(report: Report, predictions: dict) -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import text

    from app.shared.db import close_db, get_engine, ping

    report.section("5. Versioned database schema (Alembic)")
    if not await ping():
        report.add("FAIL", "Connect to Postgres", "not reachable from here (use --skip-db, or run this inside the orchestrator container)")
        await close_db()
        return

    async with get_engine().connect() as connection:
        head = ScriptDirectory.from_config(Config(str(PROJECT_ROOT / "alembic.ini"))).get_current_head()
        current = (await connection.execute(text("SELECT version_num FROM ai.alembic_version"))).scalar_one_or_none()
        report.check(current == head, "Database is at the latest Alembic migration", f"database={current} code={head}")

        rows = await connection.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'ai'"))
        existing = {row[0] for row in rows}
        missing = [t for t in TABLES if t not in existing]
        report.check(not missing, f"All {len(TABLES)} tables exist in schema ai", f"missing: {missing}" if missing else "")

        for table in ["vitals_readings", "assessments", "reviews"]:
            definition = (await connection.execute(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :name"
            ), {"name": f"{table}_label_check"})).scalar_one_or_none() or ""
            report.check(all(label in definition for label in LABELS), f"ai.{table} allows exactly the 3 mandatory labels")

        required = {
            "vitals_readings": ["label", "label_source", "model_version", "state", "country", "device_platform", "device_model", "os_version", "app_version"],
            "assessments": ["label", "label_source", "disclaimer_id", "state", "country", "app_version"],
            "motor_results": ["model_name", "model_version", "finding_type", "evidence_level", "reference"],
        }
        for table, columns in required.items():
            rows = await connection.execute(text(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'ai' AND table_name = :t"
            ), {"t": table})
            have = {row[0] for row in rows}
            missing = [c for c in columns if c not in have]
            report.check(not missing, f"ai.{table} has label source, model version, state, country and device columns" if table == "vitals_readings" else f"ai.{table} has its required columns", f"missing: {missing}" if missing else "")

        disclaimer = (await connection.execute(text("SELECT count(*) FROM ai.disclaimers WHERE id = 'disc_v1'"))).scalar_one()
        report.check(disclaimer == 1, "Versioned disclaimer disc_v1 exists")

        # ---- the requests from section 2 were saved by the worker
        report.section("5b. Requests saved by the worker (queue -> Postgres)")
        if not predictions:
            report.add("SKIP", "Saved rows", "no successful /ai/assess call in section 2")
        for name, pid in predictions.items():
            params = {"id": pid}
            saved = await wait_for_rows(connection, "SELECT count(*) FROM ai.assessments WHERE id = CAST(:id AS uuid)", params, 1)
            report.check(saved == 1, f"{name}: assessment saved", f"prediction_id={pid}")
            results = await wait_for_rows(connection, "SELECT count(*) FROM ai.motor_results WHERE assessment_id = CAST(:id AS uuid)", params, 1)
            report.check(results >= 1, f"{name}: motor results saved", f"{results} row(s)")
            usage = await wait_for_rows(connection, "SELECT count(*) FROM ai.ai_usage WHERE assessment_id = CAST(:id AS uuid)", params, 1)
            report.check(usage >= 1, f"{name}: AI cost metering row saved (ai.ai_usage)", f"{usage} row(s)")

        if "vitals" in predictions:
            row = (await connection.execute(text("""
                SELECT r.label, r.label_source, r.country, r.state, r.device_platform
                FROM ai.assessments a JOIN ai.vitals_readings r ON r.id = a.reading_id
                WHERE a.id = CAST(:id AS uuid)
            """), {"id": predictions["vitals"]})).first()
            report.check(
                row is not None and row.label in LABELS and row.country == "US" and row.device_platform == "ios",
                "Vitals reading saved with label, label source, country, state and device",
                f"{dict(row._mapping)}" if row is not None else "reading not found",
            )

        # ---- security checks that need the database
        report.section("5c. Access log and append-only records")
        if predictions:
            pid = next(iter(predictions.values()))
            audit = (await connection.execute(text("""
                SELECT actor_id, actor_role, action, reason FROM ai.audit_log
                WHERE resource_type = 'assessment' AND resource_id = :id
            """), {"id": pid})).first()
            report.check(
                audit is not None and audit.actor_id == VERIFY_USER_ID and audit.actor_role == "patient" and bool(audit.reason),
                "Access log row: who, role, action and reason",
                f"{dict(audit._mapping)}" if audit is not None else "no audit row",
            )
            # Try to change the audit row inside a transaction that is always rolled back.
            blocked = False
            transaction = await connection.begin_nested() if connection.in_transaction() else await connection.begin()
            try:
                await connection.execute(text("UPDATE ai.audit_log SET reason = reason WHERE resource_id = :id"), {"id": pid})
            except Exception as error:
                blocked = "append-only" in str(error) or "permission denied" in str(error)
            finally:
                await transaction.rollback()
            report.check(blocked, "Audit log is append-only (UPDATE is refused)")
        else:
            report.add("SKIP", "Access log row", "no successful /ai/assess call")

        triggers = (await connection.execute(text("""
            SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'ai' AND NOT t.tgisinternal AND c.relname = ANY(:tables)
        """), {"tables": APPEND_ONLY_TABLES})).scalar_one()
        report.check(triggers == 2 * len(APPEND_ONLY_TABLES), "Append-only triggers on assessments, reviews, audit_log, consents", f"{triggers} trigger(s)")
    await close_db()


# ---------------------------------------------------------------- 5. security

def tls_handshake(host: str, version: ssl.TLSVersion) -> str:
    """Connects with exactly one TLS version. Returns the negotiated version, or raises."""
    context = ssl.create_default_context()
    context.minimum_version = version
    context.maximum_version = version
    with socket.create_connection((host, 443), timeout=8) as sock:
        with context.wrap_socket(sock, server_hostname=host) as tls:
            return tls.version()


def certificate_days_left(host: str) -> int:
    context = ssl.create_default_context()
    with socket.create_connection((host, 443), timeout=8) as sock:
        with context.wrap_socket(sock, server_hostname=host) as tls:
            not_after = tls.getpeercert()["notAfter"]
    expires = datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after), tz=timezone.utc)
    return (expires - datetime.now(timezone.utc)).days


def port_is_closed(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=3):
            return False
    except OSError:
        return True


async def check_security(report: Report, client: httpx.AsyncClient, base_url: str, skip_tls: bool, token: str) -> None:
    host = urlparse(base_url).hostname
    report.section("3. Security baseline (TLS, roles, closed ports)")

    if skip_tls:
        report.add("SKIP", "TLS checks", "--skip-tls")
    else:
        try:
            version = await asyncio.to_thread(tls_handshake, host, ssl.TLSVersion.TLSv1_3)
            report.check(version == "TLSv1.3", "HTTPS works with TLS 1.3", version)
        except Exception as error:
            report.add("FAIL", "HTTPS works with TLS 1.3", f"{error!r}")
        try:
            version = await asyncio.to_thread(tls_handshake, host, ssl.TLSVersion.TLSv1_2)
            report.add("FAIL", "TLS 1.2 is refused", f"server accepted {version}")
        except (ssl.SSLError, OSError):
            report.add("PASS", "TLS 1.2 is refused")
        try:
            days = await asyncio.to_thread(certificate_days_left, host)
            report.add("PASS" if days >= 15 else "WARN", "Certificate is valid and trusted", f"{days} days left")
        except Exception as error:
            report.add("FAIL", "Certificate is valid and trusted", f"{error!r}")
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as plain:
            try:
                response = await plain.get(f"http://{host}/health")
                location = response.headers.get("location", "")
                report.check(response.status_code == 301 and location.startswith("https://"), "Plain HTTP redirects to HTTPS", f"HTTP {response.status_code} -> {location}")
            except Exception as error:
                report.add("FAIL", "Plain HTTP redirects to HTTPS", f"{error!r}")
        response = await client.get("/health")
        report.check("strict-transport-security" in response.headers, "HSTS header is sent", response.headers.get("strict-transport-security", "missing"))

    # Role-based access (the tokens are signed like the NestJS ones).
    response = await post_assess(client, {"symptoms": "test"}, None)
    report.check(response.status_code == 401, "No token -> 401", f"HTTP {response.status_code}")
    response = await post_assess(client, {"symptoms": "test"}, "not-a-real-token")
    report.check(response.status_code == 401, "Invalid token -> 401", f"HTTP {response.status_code}")
    response = await post_assess(client, {"symptoms": "test"}, make_token(UserType.User, is_refresh=True))
    report.check(response.status_code == 401, "Refresh token used as access token -> 401", f"HTTP {response.status_code}")
    for role in [UserType.Doctor, UserType.Developer, UserType.Guest]:
        response = await post_assess(client, {"symptoms": "test"}, make_token(role))
        report.check(response.status_code == 403, f"{role.name} role cannot call the patient endpoint -> 403", f"HTTP {response.status_code}")

    if host in ("localhost", "127.0.0.1"):
        report.add("SKIP", "Internal ports closed from outside", "local run: Postgres and Redis ports are open on your Mac on purpose")
    else:
        for port, what in [(8000, "orchestrator / motors"), (5432, "Postgres"), (6379, "Redis")]:
            closed = await asyncio.to_thread(port_is_closed, host, port)
            report.check(closed, f"Port {port} ({what}) is not reachable from outside")

    report.add("SKIP", "AES-256 encryption at rest", "checked in AWS: EC2 -> Volumes -> Encrypted = Yes (KMS)")


# ---------------------------------------------------------------- 6 + 7. documents, cost

def check_documents(report: Report) -> None:
    report.section("6. Milestone 1 documents")
    docs = PROJECT_ROOT / "docs"
    if not docs.exists():
        report.add("SKIP", "Documents", "docs/ is not deployed to the server; run this on your Mac to check them")
        return
    for name, what in [
        ("Vital-Signs-Database-Documentation.docx", "Schema documentation (diagram + guide)"),
        ("Labeling-Effort-Estimate.docx", "Labeling effort estimate"),
        ("AI-Cost-Estimate.docx", "AI cost benchmark and rate sheet"),
    ]:
        path = docs / name
        report.check(path.exists() and path.stat().st_size > 0, what, name)
    temp_files = [p.name for p in docs.glob("~$*")]
    report.check(not temp_files, "No Word temp files in docs/", ", ".join(temp_files))


def check_cost(report: Report) -> None:
    report.section("7. AI cost metering and routing")
    from app.configurations.models import MODELS
    from app.configurations.pricing import cost_usd
    from app.configurations.routing import ROUTES, ROUTING_VERSION

    report.check(set(ROUTES) == {"triage", "visual", "vitals"}, "Routing configured for all three motors", f"version {ROUTING_VERSION}")
    for motor, route in ROUTES.items():
        print(f"         {motor}: default={route.default} escalation={route.escalation} fallbacks={list(route.fallbacks)}")
    report.check(all(m.model_id.startswith("us.") for m in MODELS.values()), "Only US-region model IDs (patient data stays in the U.S.)")
    haiku = cost_usd("haiku", 23_430, 1_500)
    report.check(abs(haiku - 0.0034) < 0.0001, "Rate sheet matches the proposal (Haiku triage conversation = USD 0.0034)", f"USD {haiku:.4f}")
    report.add("PASS" if not config.USE_BEDROCK else "WARN", "Bedrock calls", "off (stub answers, no patient data sent to AI)" if not config.USE_BEDROCK else "ON: make sure the AWS BAA is signed")


# ---------------------------------------------------------------- main

async def main() -> int:
    parser = argparse.ArgumentParser(description="Check every Milestone 1 deliverable against the running service.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"gateway address (default {DEFAULT_BASE_URL})")
    parser.add_argument("--skip-tls", action="store_true", help="skip the HTTPS / TLS checks (local http://localhost)")
    parser.add_argument("--skip-db", action="store_true", help="skip the Postgres and Redis checks")
    parser.add_argument("--skip-rate-limit", action="store_true", help="do not send the 60-request burst")
    args = parser.parse_args()

    if not os.environ.get("JWT_SECRET"):
        print("JWT_SECRET is not set. It must be the same secret the server uses (it is in the server's .env).")
        return 1

    print(f"Milestone 1 check against {args.base_url}  ({datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC})")
    report = Report()
    token = make_token(UserType.User)

    async with httpx.AsyncClient(base_url=args.base_url, timeout=15, follow_redirects=False) as client:
        if not await check_gateway(report, client):
            print("\nThe gateway is not reachable, so the other checks were not run. Check the address, DNS and security group.")
            return report.summary()
        predictions = await check_rest(report, client, token)
        await check_security(report, client, args.base_url, args.skip_tls, token)
        if args.skip_db:
            report.section("4-5. Queue, worker and database")
            report.add("SKIP", "Queue, worker, schema and saved rows", "--skip-db")
        else:
            await check_queue(report)
            await check_database(report, predictions)
        check_documents(report)
        check_cost(report)
        if not args.skip_rate_limit:
            await check_rate_limit(report, client)  # last, so the 429s do not affect the other checks

    return report.summary()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
