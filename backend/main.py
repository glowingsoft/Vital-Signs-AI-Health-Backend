# Entry point (run with: uvicorn main:app --reload).
# Reads SERVICE and only switches on the routes for that part of the app.

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import config
from app.motors.triage.routes import router as triage_router
from app.motors.visual.routes import router as visual_router
from app.motors.vitals.routes import router as vitals_router
from app.medical_sources.controller import router as medical_sources_router
from app.orchestrator.routes import router as orchestrator_router
from app.shared import db, redis_client


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

# The allowed origin is echoed back (not "*"), so a browser that sends credentials is accepted too.
# Every origin by default; with CORS_ORIGINS only those plus any localhost frontend (app/config.py).
_any_origin = not config.CORS_ORIGINS or "*" in config.CORS_ORIGINS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[] if _any_origin else list(config.CORS_ORIGINS),
    allow_origin_regex=".*" if _any_origin else config.LOCAL_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    max_age=600,
)

# Which routers each SERVICE value switches on. include_router = like include() in Django's urls.py.
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
