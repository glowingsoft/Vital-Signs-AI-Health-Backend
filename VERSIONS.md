# Versions: one gateway, one Postgres, many backends

Several versions of the AI backend (v1, v2, v3 …) run side by side on the same server.
Each has its **own code**, and they all share **one gateway** and **one Postgres** (the same data and the same logins).
When a version is no longer needed, you switch it off. Nothing else is affected.

## Folder layout

| Folder / file | What it is | Part of |
|---|---|---|
| `gateway/` | nginx: sends `/v1/...` to v1, `/v2/...` to v2, … | **Platform** (shared) |
| `gateway/default-version.conf` | Which version answers URLs with no version (`/health`, `/ai/...`) | Platform |
| `postgres/` | First-time database setup (`bootstrap.sql`, `init.sh`) | Platform |
| `docker-compose.platform.yml` (+ `.aws.yml`, `.dev.yml`) | Starts the gateway and Postgres | Platform |
| `backend/` | **This version's code**: app, migrations, tests, Dockerfile | Version |
| `docker-compose.yml` (+ `.aws.yml`) | Starts this version: orchestrator, motors, worker, migrate, its own Redis | Version |
| `.github/workflows/platform.yml` | Deploys the platform | Platform |
| `.github/workflows/v1.yml` | Deploys v1 | Version |

## How a request travels

```
App ──► Gateway ── /v2/ai/triage ──► v2-orchestrator ──► v2's motors, v2's Redis
          │                                │
          │                                └──► Postgres (shared by every version)
          └─── /ai/triage (no version) ──► the default version (gateway/default-version.conf)
```

| URL | Goes to |
|---|---|
| `/v1/health`, `/v1/ai/...` | v1 |
| `/v2/health`, `/v2/ai/...` | v2 |
| `/health`, `/ai/...` | The default version (now v1) |
| A version that isn't running | 502 |
| Anything else (`/motor/...`, `/docs`) | 404 |

## On the server

| Part | Folder | Compose project | Deployed by |
|---|---|---|---|
| Platform (gateway + Postgres) | `/opt/vitalsigns-platform` | `vitalsigns-platform` | `platform.yml` |
| v1 | `/opt/vitalsigns-v1` | `vitalsigns-v1` | `v1.yml` (branch `V1_main`) |
| v2 | `/opt/vitalsigns-v2` | `vitalsigns-v2` | `v2.yml` (branch `v2_main`) |

All of them are on the Docker network `vitalsigns-shared`.

## Database rules (one Postgres for every version)

| Rule | Why |
|---|---|
| Only the **newest** version runs migrations (`RUN_MIGRATIONS=true`) | It knows every migration. An older one would fail with "Can't locate revision". |
| Older versions that are still running use `RUN_MIGRATIONS=false` | They use the database as it is |
| While an older version still runs, migrations only **add** things: new tables, and new columns that allow `NULL` or have a default | The older version ignores what it doesn't know |
| Don't rename or drop anything until every version that uses it is switched off | The older version would break |

Each new version branch starts from the previous one, so it already contains all the older migrations.

## Add a new version (example: v2)

| # | Step |
|---|---|
| 1 | Create the branch `v2_main` from `V1_main` (it already has `gateway/`, `postgres/`, `backend/`) |
| 2 | Make your changes in `backend/` |
| 3 | Copy `.github/workflows/v1.yml` to `v2.yml`. Change the lines marked `PER VERSION`: `branches: [v2_main]`, `API_VERSION: v2`, `APP_DIR: /opt/vitalsigns-v2`, `RUN_MIGRATIONS: "true"`. Delete `v1.yml` and `platform.yml` from the v2 branch, so the platform is deployed from one place only. |
| 4 | On `V1_main`, set `RUN_MIGRATIONS: "false"` in `v1.yml` and push (v2 now owns the migrations) |
| 5 | Push `v2_main`. `https://ai.dev.vitals-signs.com/v2/health` now works, and v1 keeps running. |

The gateway and Postgres don't change.

## Switch off an old version (example: v1)

| # | Step |
|---|---|
| 1 | Move the apps to `/v2/...` |
| 2 | Make v2 the default for URLs with no version: in `gateway/default-version.conf` change `v1` to `v2`, then deploy the platform (Actions → Deploy platform → Run workflow) |
| 3 | On the server: `cd /opt/vitalsigns-v1 && sudo docker compose -f docker-compose.yml -f docker-compose.aws.yml down` (no `-v`; only v1's Redis data would go, never Postgres) |
| 4 | Disable `v1.yml` (Actions → Deploy v1 → Disable workflow) |

After this, migrations may drop the columns that only v1 used.

## Local (your Mac)

```bash
docker network create vitalsigns-shared                    # once
docker compose -f docker-compose.platform.yml up -d         # gateway + Postgres
docker compose up -d --build                                # this version (API_VERSION from .env, default v1)
curl http://localhost/v1/health
```

To keep your existing local database, add `PGDATA_VOLUME=vital-signs-ai-health-backend_pgdata` to `.env`.
To run a second version locally, start it from another checkout with `API_VERSION=v2` and its own `REDIS_PORT`.

Tests: `cd backend && pytest`.

## Moving the server from the old all-in-one setup (once)

Before this change, everything ran as one Compose project, `vitalsigns-ai`, in `/opt/vitalsigns-ai`.

| # | Step |
|---|---|
| 1 | Run **Deploy platform**. It removes the old containers, keeps the database volume `vitalsigns-ai_pgdata`, and starts the new gateway and Postgres on it. The API is down until step 2 finishes. |
| 2 | Run **Deploy v1**. `/health` and `/v1/health` work again. |
| 3 | When everything works: `sudo rm -rf /opt/vitalsigns-ai` (old code folder only; the database is in the Docker volume) |

Don't push the old `dev` branch afterwards. Its `dev.yml` would start the old all-in-one setup again, and that clashes with the platform on ports 80 and 443.
