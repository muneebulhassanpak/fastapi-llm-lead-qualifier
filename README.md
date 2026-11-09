# Lead Qualification API

[![CI](https://github.com/muneebulhassanpak/lead-qualification-api/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/muneebulhassanpak/lead-qualification-api/actions/workflows/ci.yml)
[![Live demo](https://img.shields.io/website?url=https://lead-qualification-api-h4sy.onrender.com%2Fv1%2Fhealth&label=live%20demo&up_message=up&down_message=down)](https://lead-qualification-api-h4sy.onrender.com/docs)

FastAPI service that scores sales enquiries with an LLM, routes uncertain ones to human review, and syncs approved leads to a CRM with retries and idempotency.

> **Status:** work in progress. This is a personal portfolio project, not client work.

## Live demo

**API docs:** https://lead-qualification-api-h4sy.onrender.com/docs

Click **Authorize** and paste this public demo key:

```
lq_live_1f66cc24_22Wt_4qqAY3HB4ubOPU0tQHEDtPs2w3PGpSdhNdnqxY
```

The key can only see the demo tenant's data. It comes with eight sample leads, one in each status, and you can create your own with `POST /v1/leads`. A new lead is scored by Claude and reaches its final status within seconds.

The demo runs on Render's free plan, so after 15 idle minutes the first request takes up to a minute while the service wakes. Limits keep it cheap: 20 writes and 120 reads per minute per key, 50 model calls per day, and leads you create are deleted after 7 days. See [docs/deploy.md](docs/deploy.md) for how it is deployed.

## Run locally

```bash
cp .env.example .env
docker compose up --build
docker compose exec api python -m app.cli create-tenant --name "Demo"   # copy the printed key
```

Then open http://localhost:8000/docs, click **Authorize**, paste the key, and try `POST /v1/leads`. See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup.
