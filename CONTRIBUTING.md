# Contributing

## Workflow

`main` is protected. Every change goes through a pull request; direct pushes are rejected.

1. Open or pick an issue. Each issue belongs to a phase milestone.
2. Create a branch from `main` named `<type>/<phase>-<short-desc>`, for example `feat/03-api-keys`.
3. Commit, push, and open a PR. Fill in the PR template and link the issue (`Closes #12`).
4. CI must pass: `lint`, `typecheck`, `test` and `pr-title`. Review conversations must be resolved.
5. PRs are squash-merged, so the PR title becomes the commit message on `main`. The branch is deleted after merge.

## PR titles

PR titles follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>: <lowercase subject without a trailing period>
```

Allowed types: `feat`, `fix`, `chore`, `docs`, `test`, `refactor`, `perf`, `ci`, `build`.

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
uv run uvicorn app.main:app --reload
```

Compose publishes Postgres on host port **5433**, so it doesn't clash with a local Postgres. Change it with `POSTGRES_HOST_PORT`.

## Checks

These are the same commands CI runs:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests
uv run pytest --cov
```

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
