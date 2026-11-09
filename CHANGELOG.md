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
- CI: integration tests on Postgres, a `migrations` job (single head, upgrade, `alembic check`, downgrade, upgrade), and a Docker smoke test that runs migrations.
