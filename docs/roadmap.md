# Roadmap

Each phase ends with working code, tests, an update to the [FDE journal](fde-journal.md), and where relevant an eval report in `docs/eval-reports/`.

## ✅ Phase 1: Discovery and foundations
- [x] Problem statement, discovery notes, stakeholder map + RACI
- [x] ADRs: policy-as-code, integrate-not-replace, synthetic-data-first
- [x] Synthetic messy dataset with separate ground-truth labels
- [x] Mock ServiceNow-style API: data policies, signed at-least-once webhooks, chaos injection
- [x] Data model (`client_*` / `hao_*`) and validated policy files
- [x] CI: lint, policy validation, tests

## ✅ Phase 2: Intake (redaction and classification)
- [x] `TicketingAdapter` interface + ServiceNow adapter (retries with jitter, typed incidents, silent-ignore detection)
- [x] Platform webhook receiver: signature check, idempotency ledger, loop protection
- [x] PII redaction: context-aware regex + name heuristics, consistent tokens, in-memory vault ([ADR 0004](adr/0004-context-aware-redaction.md))
- [x] Redaction eval: recall per PII type, precision, utility
- [x] Classifier on Claude Haiku 5.5 with forced tool use; fail-safe to `unknown`
- [x] Golden sets: `golden_v1` (300 synthetic, stratified) + `hard_v1` (45 hand-written, incl. prompt injection)
- [x] Keyword baseline vs LLM harness: accuracy, risk-downgrade errors, calibration, by-style, latency, cost
- [x] LLM cost/latency recorded per call and per intake result
- [ ] **Run the Claude eval on both sets and commit the reports** (needs `ANTHROPIC_API_KEY`)

## ✅ Phase 3: Policy engine + audit log
- [x] Decision function: category level → application cap → risk-tier caps → rules → clamp; records every step ([ADR 0006](adr/0006-policy-engine-semantics.md))
- [x] Tripwire rules on ticket text, independent of the classifier
- [x] Hash-chained `hao_audit_events` with `hao audit verify / head / export`, fork prevention, external anchoring ([ADR 0007](adr/0007-hash-chained-audit-log.md))
- [x] Property-based tests: no input can ever produce A3 on FinCore/PeopleDesk, even under the most permissive valid policy
- [x] Decisions + audit trail wired into intake; `GET /decisions/{sys_id}`, `GET /audit/verify`
- [x] `hao policy simulate`: over/under-autonomy vs intended, rule ablation, what-if promotions
- [ ] Re-run the simulation with the Claude classifier (needs `ANTHROPIC_API_KEY`)

## ⏳ Phase 4: Agent lanes
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
