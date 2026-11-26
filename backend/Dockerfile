# One image for every part of ONE VERSION of the AI service: the API (any SERVICE value) and the worker.
# Built from this folder (backend/) by docker-compose.yml at the repo root.
# The command decides what runs: uvicorn (default) or "python -m app.worker".

FROM python:3.12-slim

# Do not write .pyc files; print logs immediately instead of buffering them.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install packages first: Docker caches this layer, so code changes do not reinstall everything.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY app ./app
COPY python_auth ./python_auth
# Database models and migrations, for the "migrate" container (alembic upgrade head).
COPY alembic.ini .
COPY alembic ./alembic
COPY collection ./collection
# Curated source seed library, also used as a fallback when no database is configured.
COPY db ./db
# Milestone 1 check (python -m scripts.verify_m1), run inside the orchestrator container on the server.
COPY scripts ./scripts
# Run by the "migrate" container: waits for the platform's Postgres, then alembic upgrade head.
COPY migrate.sh .

# Run as a normal user, not root. /data holds failed_enqueue.jsonl (shared by the API and the worker).
RUN useradd --create-home appuser && mkdir /data && chown appuser /data
USER appuser

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
