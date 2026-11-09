# Deploying

The public demo runs on [Render](https://render.com) (free web service, Docker) with [Neon](https://neon.tech) Postgres (free). `render.yaml` describes the service.

## How a release goes out

1. A PR is merged into `main` (rebase merge, CI green).
2. CI runs again on `main`. Render deploys that commit only once the checks pass (`autoDeployTrigger: checksPass`).
3. The new container runs `alembic upgrade head` (`RUN_MIGRATIONS=true`), then starts the API.
4. Render switches traffic once `/v1/health` returns 200. If the migration or startup fails, the previous version keeps serving.

Because the old version keeps serving while the new one starts, every migration must work with both (see the migration rules in CONTRIBUTING.md).

## First-time setup

1. **Neon:** in the project's dashboard, copy the **pooled** connection string (its host contains `-pooler`). It works as copied: the app converts `sslmode` for asyncpg and turns off prepared-statement caching behind the pooler.
2. **Render:** New → Blueprint → pick this repository. Render reads `render.yaml` and asks for the four secrets:
   - `DATABASE_URL`: the Neon pooled connection string
   - `API_KEY_PEPPER` and `WEBHOOK_SECRET_KEY`: long random strings (`python -c "import secrets; print(secrets.token_urlsafe(48))"`). To run the admin CLI from your machine against this database, use the same values locally.
   - `LLM_API_KEY`: an Anthropic key. Also set a monthly spending limit in the Anthropic console.
3. **Demo data:** from your machine, with `DATABASE_URL` and `API_KEY_PEPPER` matching Render's:
   ```bash
   uv run python -m app.cli create-tenant --name Demo      # prints the public demo key
   uv run python -m scripts.seed_demo --tenant tn_...
   ```
   The seed queues eight sample leads, which the deployed worker then qualifies and syncs.

## Demo safety

- `LLM_DAILY_CAP=50`: at most 50 model calls per tenant per day. Past that, leads go to review without a call.
- Rate limits of 20 writes and 120 reads per minute per key.
- `DEMO_RETENTION_DAYS=7`: visitors' leads are deleted after a week. The seeded samples are kept.
- The demo key only sees the Demo tenant's leads. If it is abused, revoke it with `python -m app.cli revoke-key --prefix ...` and create a new one with `create-key`.

## Free plan behaviour

The free service sleeps after 15 minutes without traffic, and the next request wakes it, which takes up to a minute. The background worker sleeps with it, so queued leads are processed once it is awake. The app starts even if Neon is also waking up, and `/v1/health` returns 503 until the database answers.
