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
- A handler subclasses `JobHandler` and is registered in `app/worker/handlers.py`. Its `run` writes commit together with the job's completion; on an exception they roll back and the job retries with backoff.
- Raise `PermanentJobError` when a retry cannot help. After the last attempt the job is `failed` and the handler's `on_dead_letter` runs.
- Handlers must be idempotent: a job can run twice if a worker dies mid-job (its lock times out after `JOB_LOCK_TIMEOUT_SECONDS`).

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
uv run mypy app tests
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
