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
