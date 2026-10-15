# One image for every part of the AI service: the API (any SERVICE value) and the worker.
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
# Database models and migrations, for the "migrate" container (alembic upgrade head).
COPY alembic.ini .
COPY alembic ./alembic
COPY collection ./collection

# Run as a normal user, not root. /data holds failed_enqueue.jsonl (shared by the API and the worker).
RUN useradd --create-home appuser && mkdir /data && chown appuser /data
USER appuser

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
