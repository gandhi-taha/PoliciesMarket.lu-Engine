FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# build-essential + libpq-dev: psycopg compile deps
# postgresql-client: ships pg_isready, used by docker-entrypoint.sh
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev postgresql-client curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md providers.yaml /app/
COPY app /app/app
COPY alembic /app/alembic
COPY alembic.ini /app/alembic.ini
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

RUN pip install --no-cache-dir -U pip && pip install --no-cache-dir -e .

EXPOSE 8000
# Migrations + seed run on every boot (both are idempotent). Pass any
# server flags after the entrypoint, e.g. `--reload` in dev.
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
