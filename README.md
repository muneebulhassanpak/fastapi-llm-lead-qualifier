# Lead Qualification API

FastAPI service that scores sales enquiries with an LLM, routes uncertain ones to human review, and syncs approved leads to a CRM with retries and idempotency.

> **Status:** work in progress. This is a personal portfolio project, not client work.

## Run locally

```bash
cp .env.example .env
docker compose up --build
```

Then open http://localhost:8000/docs. See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup.
