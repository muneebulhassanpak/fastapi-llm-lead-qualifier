# Contributing

## Workflow

`main` is protected. Every change goes through a pull request; direct pushes are rejected.

1. Open or pick an issue. Each issue belongs to a phase milestone.
2. Create a branch from `main` named `<type>/<phase>-<short-desc>`, for example `feat/03-api-keys`.
3. Make small commits, one logical step each, with its tests. Every commit should pass lint, format and type checks on its own.
4. Push and open a PR. Fill in the PR template and link the issue (`Closes #12`).
5. CI must pass: `lint`, `typecheck`, `test`, `migrations`, `docker`, `pr-title` and `commit-messages`. Review conversations must be resolved.
6. PRs are **rebase-merged**: every commit lands on `main` as it is, and history stays linear. Rebase on `main` and squash any fixup commits before merging. The branch is deleted after merge.

## Commit messages and PR titles

Both follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<optional scope>): <subject>

<optional body: why, not what>
```

Types: `feat`, `fix`, `chore`, `docs`, `test`, `refactor`, `perf`, `ci`, `build`, `revert`.
Scopes used here: `api`, `db`, `worker`, `llm`, `crm`, `webhooks`.
Subjects start lowercase, have no trailing period, and stay under 100 characters.

To fix an earlier commit on your branch, use `git commit --fixup=<sha>` and then `git rebase --autosquash main`. CI rejects leftover fixup commits.

## Definition of done

- CI is green.
- New behaviour has tests. Bug fixes include a test that fails without the fix.
- No secrets in code. New settings are added to `.env.example`.
- Database changes include an Alembic migration.
- API changes include OpenAPI descriptions and examples.
- `CHANGELOG.md` is updated under `Unreleased`.

## Local setup

Requirements: [uv](https://docs.astral.sh/uv/), Docker, and Python 3.12 (uv can install it).

```bash
cp .env.example .env          # then fill in any secrets you need
uv sync                       # create .venv with app + dev dependencies
uv run pre-commit install     # run ruff and mypy before every commit
```

Run the whole stack (API + Postgres) in Docker:

```bash
docker compose up --build
curl localhost:8000/v1/health
open http://localhost:8000/docs
```

Or run the API on your machine with auto-reload:

```bash
docker compose up -d db
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Compose publishes Postgres on host port **5433**, so it doesn't clash with a local Postgres. Change it with `POSTGRES_HOST_PORT`.

> Commands run on your machine (`uvicorn`, `alembic`, the CLI) use `DATABASE_URL` from `.env`. If that points at a hosted database such as Neon, they act on it. `docker compose` always uses its own database container.

## Tenants and API keys

Tenants and keys are created from the command line, never through the API:

```bash
uv run python -m app.cli create-tenant --name "Acme"      # prints the key once
uv run python -m app.cli create-key --tenant tn_...
uv run python -m app.cli revoke-key --prefix ab12cd34
uv run python -m app.cli list-tenants

# inside the compose stack
docker compose exec api python -m app.cli create-tenant --name "Acme"
```

Keys are stored as an HMAC-SHA256 hash keyed with `API_KEY_PEPPER`. Changing the pepper invalidates every existing key.

## Background jobs

Slow work (LLM scoring, CRM sync) runs as jobs in the `jobs` table, processed by a worker that runs inside the API process (`WORKER_ENABLED=true`).

- Enqueue with `app.worker.queue.enqueue(session, ...)` in the same transaction as the change that needs it.
- A handler subclasses `JobHandler` (`app/worker/handlers.py`) and is registered in `app/worker/registry.py`. Its `run` writes commit together with the job's completion; on an exception they roll back and the job retries with backoff.
- Raise `PermanentJobError` when a retry cannot help. After the last attempt the job is `failed` and the handler's `on_dead_letter` runs.
- Handlers must be idempotent: a job can run twice if a worker dies mid-job (its lock times out after `JOB_LOCK_TIMEOUT_SECONDS`).

## LLM qualification

The `qualify_lead` handler (`app/worker/qualify.py`) asks the provider chosen by `LLM_MODE` for a structured `Qualification`, then `decide()` in `app/domain/qualification.py` routes the lead using `AUTO_APPROVE_SCORE`, `DISQUALIFY_SCORE` and `MIN_CONFIDENCE`.

- The prompt lives in `app/llm/prompts/qualify_v1.md`. Changing its meaning means a new file and a new `PROMPT_VERSION`, so every `scored` event says which prompt produced it.
- The lead's text only ever goes inside `<enquiry>` tags in the user message, never in the system prompt.
- Invalid model output gets one more try; refusals and rejected requests go straight to review; timeouts, rate limits and 5xx errors retry the job with backoff, and the lead goes to review once attempts run out.
- Tests use the `mock` provider or a scripted fake and never call a real model. To check the real one (it costs money):

```bash
# in .env: LLM_MODE=anthropic and LLM_API_KEY=...
uv run pytest -m live_llm
```

## CRM sync

Approving a lead (automatically or, from Phase 07, by a person) queues a `sync_lead` job in the same transaction. The handler (`app/worker/sync.py`) moves the lead `approved` → `syncing` → `synced`, calling the adapter chosen by `CRM_MODE` with no transaction open, and writes one `sync_attempts` row per try.

- Adapters implement `CRMAdapter.upsert_lead` (`app/crm/base.py`) keyed by our lead ID, so a job that runs twice still leaves one CRM record.
- `CRMPermanentError` (a 4xx) dead-letters at once; `CRMTransientError` retries with backoff, waiting at least its `retry_after`. Up to `CRM_MAX_ATTEMPTS` tries, then the lead is `sync_failed`.
- v1 ships only the mock adapter, which writes to `mock_crm_records`. To see failures, set `MOCK_CRM_FAIL_RATE`, or create a lead with `"metadata": {"simulate_crm_failure": "unavailable"}` (503), `"rate_limited"` (429) or `"invalid"` (422).

## Webhooks

Outside services post enquiries to `POST /v1/webhooks/{provider}/{endpoint_id}`. Each endpoint belongs to one tenant and has its own signing secret, stored encrypted with `WEBHOOK_SECRET_KEY`.

```bash
uv run python -m app.cli create-webhook --tenant tn_... --provider generic   # or tally
export WEBHOOK_SECRET=whsec_...    # the secret it printed
uv run python -m scripts.send_webhook --endpoint whk_... --provider generic
```

- **generic:** `X-Timestamp` (unix seconds) and `X-Signature: sha256=<hex HMAC-SHA256 of "{timestamp}.{body}">`. Deliveries more than 5 minutes old or in the future are refused. Body: `{"event_id": "...", "lead": {...same fields as POST /v1/leads...}}`.
- **tally:** paste the endpoint URL and the secret into the form's webhook settings (Tally signs with `Tally-Signature`). Long-text answers become the message, and other answers are added as `Label: value` lines.
- Signatures are checked over the raw body, compared in constant time. Every failure is the same `401`.
- The provider's event ID is the idempotency key for 72 hours, so a redelivery returns the original lead (`200`).
- Bodies over 64 KB get `413`.
- To add a provider: write an adapter with `verify` and `parse` (`app/webhooks/base.py`), add it to `WebhookProvider` and `ADAPTERS`, and add a fixture payload from the provider's docs.

## Rate limits

Every API key has a per-minute limit for reads (`RATE_LIMIT_READS_PER_MINUTE`) and for writes (`RATE_LIMIT_WRITES_PER_MINUTE`, which also covers review actions). Each webhook endpoint has the write limit, counted only after its signature checks out. Responses carry `X-RateLimit-Limit`, `-Remaining` and `-Reset`; over the limit is `429` with `Retry-After`.

Counts are kept in memory over a sliding window, per process. With several instances, each counts separately; scaling out would mean moving the counters to Redis or Postgres.

## Database migrations

```bash
uv run alembic revision --autogenerate -m "add jobs table"   # then read and edit the file
uv run alembic upgrade head
uv run alembic check          # fails if models changed without a migration
```

Rules (CI enforces the first three):

1. Every model change ships with a migration in the same PR. CI runs `alembic check`.
2. Every migration has a working `downgrade`. CI runs upgrade → downgrade → upgrade on an empty database.
3. There is exactly one head. If two PRs both add a migration, the second one rebases and runs `alembic merge` (or re-parents its migration).
4. Never edit a migration that is already on `main`. Add a new one.
5. The running version must keep working on the new schema, because it serves traffic while the new version starts. Add columns as nullable or with a default; remove columns in a later release.
6. Migrations that change data must be safe to run twice.

## Checks

These are the same commands CI runs:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests scripts
uv run pytest --cov            # needs the db container: docker compose up -d db
```

Integration tests create a fresh `leadqual_test` database on every run (`TEST_DATABASE_URL`) and roll back each test's changes. They never use `DATABASE_URL`. Coverage below 90% fails the run, and so does any warning.

## Code layout

| Path | Holds |
|---|---|
| `app/api/` | HTTP only: routers, request/response models, dependencies |
| `app/services/` | Use cases (create lead, approve, ...) |
| `app/domain/` | Pure business rules with no I/O (statuses, routing, backoff) |
| `app/db/` | Engine, session, models |
| `app/worker/` | Job queue and handlers |
| `app/llm/`, `app/crm/`, `app/webhooks/` | Adapters for outside systems |
| `tests/` | `unit/` runs without services; `integration/` needs Postgres |
