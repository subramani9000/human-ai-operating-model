# Roadmap

Each phase ends with working code, tests, an update to the [FDE journal](fde-journal.md), and where relevant an eval report in `docs/eval-reports/`.

## ✅ Phase 1: Discovery and foundations
- [x] Problem statement, discovery notes, stakeholder map + RACI
- [x] ADRs: policy-as-code, integrate-not-replace, synthetic-data-first
- [x] Synthetic messy dataset with separate ground-truth labels
- [x] Mock ServiceNow-style API: data policies, signed at-least-once webhooks, chaos injection
- [x] Data model (`client_*` / `hao_*`) and validated policy files
- [x] CI: lint, policy validation, tests

## ⏳ Phase 2: Intake (redaction and classification)
- [ ] `TicketingAdapter` interface + adapter for the mock API (retries, idempotent webhook receiver)
- [ ] PII redaction: Microsoft Presidio plus custom recognisers (`KVR-#####` employee IDs, +91 phones), with a reversible token vault kept inside the client boundary
- [ ] Redaction eval: recall per PII type against `labels.jsonl` (target ≥ 99% on phones and employee IDs)
- [ ] Classifier on Claude Haiku 5.5 with structured output: category, application, priority, confidence
- [ ] 300-ticket golden set (stratified by category × style); report accuracy, per-class F1, confidence calibration
- [ ] Baseline comparison: keyword rules vs. LLM. Is the LLM worth its cost?
- [ ] LLM cost/latency logging per call

## Phase 3: Policy engine + audit log
- [ ] Decision function: category level → application cap → rules → clamp; records applied rules
- [ ] Hash-chained `hao_audit_events` with a `hao audit verify` command that detects tampering
- [ ] Property-based tests: no input can ever produce A3 on FinCore/PeopleDesk

## Phase 4: Agent lanes
- [ ] LangGraph workflow with human-in-the-loop interrupts for A2
- [ ] A3 tools against the mock API (reset, unlock, resolve with close notes, reassign)
- [ ] A1 handoff packet: summary, similar resolved tickets (pgvector), checks done, suggested next step
- [ ] Review console (Streamlit): approve / edit / reject with reason codes
- [ ] Claude Sonnet 5.5 for resolution drafting and handoff summaries

## Phase 5: Learning loop
- [ ] Feedback capture from the console and from reopen events in the ticketing system
- [ ] Autonomy controller: nightly precision per category, promotion proposals, automatic demotion
- [ ] Leadership dashboard: volume by level, precision, reopen rate, resolution time, cost per ticket, **human hours redirected**

## Phase 6: Hardening and handover
- [ ] Docker Compose for the full stack; Langfuse tracing
- [ ] CI eval gate: block merges that regress golden-set accuracy or redaction recall
- [ ] Runbook (on-call, rollback a promotion, rotate secrets) and pilot readout with before/after metrics
