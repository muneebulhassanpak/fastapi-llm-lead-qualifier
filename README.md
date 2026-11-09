# Lead Qualification API

FastAPI service that scores sales enquiries with an LLM, routes uncertain ones to human review, and syncs approved leads to a CRM with retries and idempotency.

> **Status:** work in progress. This is a personal portfolio project, not client work.

## Run locally

```bash
cp .env.example .env
docker compose up --build
docker compose exec api python -m app.cli create-tenant --name "Demo"   # copy the printed key
```

Then open http://localhost:8000/docs, click **Authorize**, paste the key, and try `POST /v1/leads`. See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup.
