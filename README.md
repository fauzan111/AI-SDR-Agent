# AI Sales Development Representative (SDR) Agent

An agent that engages inbound leads, qualifies them against a defined ICP, enriches
them with public signals, and pushes a structured, qualified lead into a CRM — with
**idempotent webhook handling so a retried request never creates a duplicate lead.**
This is the single most common "AI agent for startups" pitch, built with the
integration rigor that separates it from a demo.

Built on [`agent-platform-foundation`](../agent-platform-foundation) — same
multi-tenancy, audit-log, provider-agnostic-LLM, and observability spine.

## The engineering that makes it production, not a demo

**1 · Idempotency (the real challenge).** A naive agent that processes the same
lead twice creates duplicate CRM entries. Here, dedupe is defense-in-depth:

- Fast path: an `IdempotencyRecord` lookup on `(tenant_id, idempotency_key)`.
- Race path: a **DB `UNIQUE(tenant_id, idempotency_key)` constraint** — two
  concurrent duplicates can't both insert; the loser catches `IntegrityError`,
  rolls back (dropping its orphan lead in the same transaction), and returns the
  winner's lead.
- The Celery worker **re-validates the lead's state before acting** (`acks_late`
  + idempotent pipeline), so a redelivered message is a safe no-op — it never
  just trusts the queue.

```
$ python scripts/retry_test.py --concurrency 20
concurrent requests: 20
distinct lead ids:   1
leads in DB w/ id:   1
PASS: exactly one lead created under concurrent retries
```

**2 · Hybrid execution in qualification.** Objective ICP criteria (company size,
industry) are **deterministic hard filters in code** that short-circuit *before
any LLM call* — you never want a model deciding whether a 3-person company clears
a 50-employee floor. Only ICP-passing leads get an **LLM-judged conversational
fit** score. A disqualified lead's `decided_by == "code"`; a scored lead's fit is
`"llm"`. Both are enforced by tests.

**3 · Real CRM integration shape.** The CRM adapter is built against **HubSpot's
v3 contacts email-keyed upsert** API. `CRM_PROVIDER=mock` runs offline
(deterministic id from email = same idempotent semantics); `CRM_PROVIDER=hubspot`
+ a token hits the real API — so "integrates with HubSpot" is honest.

## Architecture

```
Inbound lead (webhook / form)
   → dedupe check  (idempotency key; DB unique constraint)        [code]
   → Qualification agent:  hard filters [code] → conversational fit [LLM]
   → Enrichment:  fill company/size/industry from public signals  [adapter]
   → structured Lead (Pydantic: score, notes, next_action)
   → CRM push  (HubSpot email-keyed upsert — idempotent)          [adapter]
   → meeting-scheduling handoff (calendar) — qualified leads only [adapter]
   → every step → AuditLog (tenant_id, decided_by)
Retry-safe background processing: Celery task, tenant-scoped, re-validates state.
```

## Runs fully offline (no API key)

No LLM key → conversational fit uses a keyword heuristic. `CRM_PROVIDER=mock` →
deterministic mock HubSpot. `PROCESS_INLINE=true` → no Redis/worker needed. So
the whole flow runs from a single container with zero secrets.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pytest tests/unit -q            # 11 tests, no external services

make run                        # http://localhost:8000  (offline, inline processing)
make demo                       # seeds qualify / disqualify / dedupe leads
make retrytest                  # proves idempotency under concurrency

# Full async stack (Postgres + Redis + Celery worker, PROCESS_INLINE=false):
docker compose up --build
```

Open **http://localhost:8000/** for the console (define ICP → submit a lead →
click **"Re-send same lead"** to watch dedupe fire), or **/docs** for Swagger.

### API

| Method | Path | Purpose |
|---|---|---|
| POST | `/tenants/signup` | Create a workspace with ICP config; returns a token |
| POST | `/webhooks/leads` | Inbound lead (idempotent; `Idempotency-Key` header optional) |
| GET  | `/leads` | List this tenant's leads |
| GET  | `/leads/{id}` | Lead detail |
| GET  | `/health` | Liveness |

## Deploy live (Render, one blueprint)

The repo ships a [`render.yaml`](./render.yaml): **New → Blueprint → connect this
repo → Apply**. Render provisions the web service + Postgres and auto-deploys on
every push. `PROCESS_INLINE=true` keeps it single-service (no Redis) for the free
tier. Add `ANTHROPIC_API_KEY` for LLM-judged fit and `CRM_PROVIDER=hubspot` +
`HUBSPOT_ACCESS_TOKEN` for real HubSpot pushes in the Environment tab.

> Free-tier notes: the web service sleeps after ~15 min idle (~30s cold start);
> free Postgres expires after 90 days.

## Tech stack

Python 3.12 · FastAPI · LangGraph · SQLAlchemy · Celery/Redis · structlog ·
Docker Compose · GitHub Actions · pytest. CRM adapter targets HubSpot v3.
