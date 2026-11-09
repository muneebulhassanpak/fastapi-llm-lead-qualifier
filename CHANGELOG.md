# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Repository workflow: PR and issue templates, CODEOWNERS, contributing and security guides.
- CI baseline with PR title check, and Dependabot for GitHub Actions.
- `.env.example` listing every planned setting.
- FastAPI application skeleton with `/v1/health` and OpenAPI metadata.
- Typed settings with fail-fast validation of production secrets.
- Request IDs (`X-Request-ID`) on every response and in every log line; structured JSON logs.
- A single error format for all error responses.
- ruff, strict mypy, pytest with coverage, and pre-commit hooks; real CI checks plus a Docker build and smoke test.
- Multi-stage Docker image running as a non-root user, and docker-compose with Postgres 16.
- Postgres schema with Alembic migrations: tenants, hashed API keys, leads, an append-only audit log, idempotency keys.
- `X-API-Key` authentication and an admin CLI to create tenants and create or revoke keys.
- `POST /v1/leads` (202, optional `Idempotency-Key`), `GET /v1/leads` (filters, cursor pagination), `GET /v1/leads/{id}` (detail with audit trail). Tenants only ever see their own leads.
- Lead status rules in one place; every status change records an audit event.
- `/v1/health` checks the database and returns 503 when it is unreachable.
- Neon and other hosted Postgres connection strings work as copied.
- Background job queue on a Postgres table, claimed with `FOR UPDATE SKIP LOCKED`, with exponential backoff and jitter, a dead-letter state, and recovery of jobs whose worker died mid-run.
- An in-process worker started by the API; handler writes and job completion commit together. Creating a lead queues its qualification job in the same transaction.
- CI: integration tests on Postgres, a `migrations` job (single head, upgrade, `alembic check`, downgrade, upgrade), and a Docker smoke test that runs migrations.
- LLM qualification: each new lead is scored by Anthropic (default `claude-haiku-5-5`), OpenAI or an offline mock provider using structured outputs, then auto-approved, disqualified or sent to review with a recorded reason.
- A versioned prompt that keeps lead text out of the instructions, and opt-in live tests (`pytest -m live_llm`) including prompt-injection attempts.
- A per-tenant daily cap on model calls, with token usage recorded per tenant and day.
- CRM sync: approved leads are pushed to the CRM by a background job, with one row per attempt, retries with backoff (respecting `Retry-After`), and `sync_failed` when attempts run out. Upserts are keyed by our lead ID, so retries never duplicate a record.
- A mock CRM that can fail on purpose (a fail rate, or a per-lead 503, 429 or 422) to show the retry path.
- `GET /v1/leads/{id}` shows the CRM provider, external ID, attempt count and last error.
- Review endpoints: `POST /v1/leads/{id}/approve` (optional note and score override) and `/reject` for leads in `needs_review`, and `/retry` to re-sync a `sync_failed` lead or re-qualify one the model failed on. Every action is audited with the caller's key; concurrent actions on one lead give one success and a `409` with the current status.
- Signed inbound webhooks at `POST /v1/webhooks/{provider}/{endpoint_id}`, for a generic format (timestamped HMAC, 5-minute replay window) and Tally forms. Secrets are encrypted at rest, deliveries are deduplicated by event ID, and bodies are limited to 64 KB. CLI commands `create-webhook` and `revoke-webhook`, and `scripts/send_webhook.py` to try them.
- Per-key rate limits for reads and writes, and per-endpoint limits for webhooks, with `X-RateLimit-*` headers and `429` plus `Retry-After`.
