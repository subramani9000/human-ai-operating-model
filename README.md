# Human+AI Operating Model

[![ci](https://github.com/subramani9000/human-ai-operating-model/actions/workflows/ci.yml/badge.svg)](https://github.com/subramani9000/human-ai-operating-model/actions/workflows/ci.yml)

**A simulated forward-deployed engineering (FDE) engagement:** designing and building the operating model that decides, for every piece of enterprise work, *whether AI or a human does it, at what level of autonomy, under whose ownership, and how that changes as evidence comes in.*

The client, **Kaveri Logistics**, is fictional. The problems are not: three disconnected AI pilots, a chatbot that closes tickets nobody reviewed, an audit team that can't get a log, and service-desk agents who don't trust any of it.

> 📄 Start with the [problem statement](docs/00-problem-statement.md), then the [discovery notes](docs/01-discovery-notes.md).

---

## The idea in one picture

```mermaid
flowchart LR
    T[Client ticketing system] -->|webhook| I[Intake + PII redaction]
    I --> P{Policy engine}
    P -->|A3| A3[AI acts]
    P -->|A2| A2[AI drafts → human approves]
    P -->|A1| A1[Human leads, AI assists]
    P -->|A0| A0[Human only]
    A3 & A2 & A1 & A0 --> L[(Audit log + feedback)]
    L --> C[Autonomy controller]
    C -.->|earn / lose autonomy| P
```

| Level | Meaning | Example |
|---|---|---|
| **A3** | AI acts on its own | Password reset on SSO |
| **A2** | AI drafts, a human approves | Anything touching payroll (permanent cap) |
| **A1** | Human leads, AI assists with context | App errors escalated to L2 |
| **A0** | Human only | Phishing reports, site outages |

The operating model is **code**: [`policy/autonomy.yaml`](policy/autonomy.yaml) defines levels, caps, owners and rules. Every decision records the policy version that produced it. Autonomy is **earned**: a category is promoted only when its measured precision clears the bar *and* its named human owner approves. It is demoted automatically the moment quality drops.

---

## Status

| Phase | Scope | Status |
|---|---|---|
| **1. Discovery and foundations** | Engagement docs, synthetic messy dataset, mock ServiceNow-style API, data model, validated policy | ✅ Done |
| 2. Intake | PII redaction (Presidio + custom recognisers), Claude-based classifier, golden-set evals | ⏳ Next |
| 3. Policy engine + audit | Autonomy decisions, hash-chained tamper-evident audit log | |
| 4. Agent lanes | LangGraph workflows for A3/A2/A1, handoff packets, review console | |
| 5. Learning loop | Feedback capture, autonomy controller, leadership dashboard | |
| 6. Hardening | Docker, CI eval gates, Langfuse tracing, runbook, pilot readout | |

See [docs/roadmap.md](docs/roadmap.md) for details.

---

## Quickstart

```bash
git clone https://github.com/subramani9000/human-ai-operating-model.git
cd human-ai-operating-model
pip install -e ".[dev]"

hao policy validate      # check the operating-model policy
hao generate             # 5,000 synthetic tickets + ground-truth labels (seed 42)
hao profile              # data-quality profile of the dataset
pytest -q                # 32 tests
hao mock serve           # mock client ticketing API → http://localhost:8001/docs
```

Or with Docker (Postgres + mock API): `docker compose up --build`

### Talk to the mock client system

```bash
H='X-API-Key: kaveri-dev-key'

# Load the synthetic backlog
curl -X POST localhost:8001/admin/seed -H "$H"

# ServiceNow-style encoded queries
curl -G localhost:8001/api/now/table/incident -H "$H" \
  --data-urlencode 'sysparm_query=assignment_group=HR-Systems^ORDERBYDESCopened_at' \
  --data-urlencode 'sysparm_limit=5'

# Subscribe to signed webhooks
curl -X POST localhost:8001/api/x_kaveri/webhooks -H "$H" \
  -H 'Content-Type: application/json' -d '{"url": "http://localhost:9000/hooks"}'

# Try to resolve without close notes and watch the data policy reject it
curl -X PATCH localhost:8001/api/now/table/incident/<sys_id> -H "$H" \
  -H 'Content-Type: application/json' -d '{"state": "6"}'
```

---

## What makes this realistic

**The mock client system is deliberately not clean** ([ADR 0002](docs/adr/0002-integrate-not-replace.md)):

- Every field is a string (`"true"`, `"3"`, `""`), datetimes have no timezone, responses are wrapped in `{"result": ...}`
- Resolving requires `close_code` and `close_notes` (data policy), and closed tickets are immutable
- **Unknown fields are silently ignored.** Misspell `assignment_group` and nothing tells you
- Webhooks are HMAC-signed, retried with backoff and **at-least-once** (`MOCK_WEBHOOK_DUPLICATE_RATE`)
- Injectable outages and latency (`MOCK_FAILURE_RATE`, `MOCK_LATENCY_MS`)

**The synthetic data reproduces what discovery found on the floor** ([ADR 0003](docs/adr/0003-synthetic-data-first.md)):

```
Data quality (what the AI has to cope with):
  user category blank         39.9%
  user category 'Other'       19.0%
  needs re-routing            62.5%
  contains PII                59.4%
Style mix: formal 45.2%, terse 24.4%, tanglish 13.3%, hinglish 10.0%, angry 7.1%
```

Tickets like *"WMS-Pro open panna 'Scanner sync failed: code WMS-4021' nu error varudhu"*, forwarded email chains, signatures with phone numbers and employee IDs, and near-duplicates from impatient callers. Ground-truth labels live in a **separate file** so evals never leak into the system under test.

**The policy can't be configured into something unsafe.** Validation rejects a payroll category set to A3, a category without a named owner, or controller thresholds that would make autonomy flap. CI runs `hao policy validate` on every PR.

---

## Repository layout

```
docs/                     The engagement trail: problem, discovery, stakeholders, design, ADRs, journal
policy/                   autonomy.yaml (levels, caps, rules), ownership.yaml (named owners)
src/hao/
  synthetic/              Deterministic messy-ticket generator + noise model
  mock_ticketing/         ServiceNow-style client API, signed webhooks, chaos injection
  policy/                 Policy schema + cross-validation
  db/                     Data model (client_* = simulated client, hao_* = platform)
  cli.py                  `hao` command
data/synthetic/           Committed 200-ticket sample (full set via `hao generate`)
tests/                    Generator, policy and API behaviour
```

## FDE skills this project exercises

- **Discovery under conflict:** sponsor vs. audit vs. HR, resolved in the design rather than ignored ([discovery notes](docs/01-discovery-notes.md), [stakeholder map](docs/02-stakeholder-map.md))
- **Integrating with systems you don't own:** adapters, idempotency, signed webhooks, failure injection
- **Getting unblocked:** synthetic data with exact ground truth while legal reviews historical data
- **Governance as engineering:** policy-as-code, named owners, auditable decisions
- **Evidence-driven autonomy:** evals that decide what AI is allowed to do (Phases 2–5)

Lessons learned along the way are in the [FDE journal](docs/fde-journal.md).

## License

MIT
