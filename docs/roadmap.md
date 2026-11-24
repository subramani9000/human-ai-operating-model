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

## ✅ Phase 4: Agent lanes
- [x] Lanes A0–A3 over a durable work-item state machine (chosen over LangGraph: [ADR 0008](adr/0008-agent-lanes-and-tools.md))
- [x] Playbooks: per-category tool allowlists; "no playbook, no autonomy"
- [x] Tools against the mock: password reset, unlock, reply, resolve, software request, routing. Guarded at execution time, plus client-side IAM refusal on HR/finance apps
- [x] A1 handoff packet: summary, KB article, similar tickets (BM25), suggested next step
- [x] Drafters: template + Claude Sonnet 5.5, with `check_draft` guardrails
- [x] Review console: approve / edit / reject with reason codes; re-checks policy; runs under the approver's name ([ADR 0009](adr/0009-review-console.md))
- [x] QA sampling of A3 executions, pass / fail feedback
- [ ] Run the Claude drafter on real-looking tickets and review its drafts in the console (needs `ANTHROPIC_API_KEY`)

## ✅ Phase 5: Learning loop
- [x] Feedback capture: review verdicts, QA verdicts, and reopens from the client's webhooks (idempotent, loop-safe)
- [x] Autonomy controller ([ADR 0010](adr/0010-autonomy-controller.md)): per-item evidence since the last level change; automatic demotion via runtime overrides; owner-approved promotions that edit `autonomy.yaml` for the PR
- [x] Policy hot-reload (`POST /policy/reload`) with a `policy.loaded` audit event
- [x] Leadership dashboard (`/dashboard`): pilot targets, level mix, outcomes, override and reopen rates, **human hours redirected** with stated assumptions (`policy/value.yaml`), audit integrity
- [x] Shared clock + `hao pilot simulate`: weeks of traffic through the real system with simulated reviewers, QA, users and owners → [simulated pilot readout](pilot-readout-simulated.md)
- [ ] Re-run the pilot simulation with `--classifier claude` (needs `ANTHROPIC_API_KEY`)

## ⏳ Phase 6: Hardening and handover
- [ ] Docker Compose for the full stack; Langfuse tracing
- [ ] CI eval gate: block merges that regress golden-set accuracy or redaction recall
- [ ] SSO in front of `/console` and `/dashboard`; reviewer identity asserted, not typed ([ADR 0009](adr/0009-review-console.md))
- [ ] Sweep for drafts unreviewed after N hours → hand off to a human
- [ ] Runbook (on-call, rollback a promotion, clear a demotion, rotate secrets)
