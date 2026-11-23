# Entry point (run with: uvicorn main:app --reload).
# Reads SERVICE and only switches on the routes for that part of the app.

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import config
from app.motors.triage.routes import router as triage_router
from app.motors.visual.routes import router as visual_router
from app.motors.vitals.routes import router as vitals_router
from app.orchestrator.consent_check import ConsentError, consent_error_response
from app.medical_sources.controller import router as medical_sources_router
from app.orchestrator.routes import router as orchestrator_router
from app.shared import db, redis_client

<<<<<<< Updated upstream
=======
logger = logging.getLogger(__name__)
from fastapi.middleware.cors import CORSMiddleware

>>>>>>> Stashed changes

# runs at start up and shutdown of the FastAPI app, to open and close connections to Redis and Postgres.
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── PART 1: STARTUP ──  (runs once, before any request is served)

    # Code before "yield" runs at startup, code after it at shutdown.
    # Redis and the database connect lazily (on first use), so there is nothing to open here.
    yield
    # ── PART 2: SHUTDOWN ──  (runs once, after the server is told to stop)

    await redis_client.close_redis()
    await db.close_db()


# FastAPI() = the whole web app, like Django's project. /docs is generated from it automatically.
app = FastAPI(title=f"Vital Signs AI ({config.SERVICE})", lifespan=lifespan)

# Consent refusals answer {"code", "message", "missing"} instead of FastAPI's {"detail": ...}.
app.add_exception_handler(ConsentError, consent_error_response)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Which routers each SERVICE value switches on. include_router = like include() in Django's urls.py.
# The medical source admin API runs with the orchestrator: the gateway sends every /ai/ request there.
ROUTERS = {
    "orchestrator": [orchestrator_router, medical_sources_router],
    "triage": [triage_router],
    "visual": [visual_router],
    "vitals": [vitals_router],
}

for name, routers in ROUTERS.items():
    if config.SERVICE in ("all", name):
        for router in routers:
            app.include_router(router)


@app.get("/health")
async def health() -> dict:
    # Every service has this, so a load balancer (later) can check each container is alive.
    # Always HTTP 200: the API itself is up even when Redis or Postgres is down; the body says which.
    return {
        "status": "ok",
        "service": config.SERVICE,
        "redis": "ok" if await redis_client.ping() else "down",
        "database": "ok" if await db.ping() else "down",
    }
