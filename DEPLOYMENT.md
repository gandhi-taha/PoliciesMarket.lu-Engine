# Deployment

This service is designed to run as a FastAPI API behind the existing Next.js platform.

## Local development
1. Start Postgres with `docker compose up db`.
2. Apply migrations with `alembic upgrade head`.
3. Seed sample provider and knowledge data with `python -m app.db.seed`.
4. Start the API with `uvicorn app.main:app --reload`.

On Windows PowerShell, activate the workspace virtual environment with:

```
. ..\.venv\Scripts\Activate.ps1
```

If Docker Desktop is not running, `docker compose up db` will fail until the daemon is available.

The local database image includes the pgvector extension so both full-text and vector retrieval work out of the box.

## Container deployment
- Build the image with the provided Dockerfile.
- Set `DATABASE_URL` to the managed Postgres instance.
- Set `MISTRAL_API_KEY` only if query parsing or RAG answers should use Mistral.
- Keep the Next.js application responsible for user-facing UI and call this engine over HTTP.
- Use `docker compose up --build` for local stack bring-up when you want the API and Postgres together.

## Operational notes
- `/health/live` is for container liveness.
- `/health/ready` is for readiness checks.
- Use Alembic for all schema changes.
- Seed data is intended for local development and MVP testing only.
