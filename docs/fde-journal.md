# FDE journal

A running log of what this engagement teaches about forward-deployed engineering: decisions, surprises and what I'd do differently. One entry per phase.

---

## Phase 1 — Discovery and foundations

**What was built**
- Problem statement, discovery notes, stakeholder map and RACI
- Three ADRs: policy-as-code, integrate-not-replace, synthetic-data-first
- Synthetic messy ticket generator with separate ground-truth labels
- Mock ServiceNow-style ticketing API with signed webhooks and failure injection
- Data model and validated policy files

**FDE lessons**
1. **The ask is rarely the problem.** The client asked to "fix the chatbot." The real problem was the absence of decision rights and accountability around *all* their AI. Discovery is where the engagement is won.
2. **Stakeholders conflict, and that's the job.** Sponsor wants speed, audit wants traceability, HR wants job security. The design has to make each of them a winner (see the conflict table in the discovery notes), not pick one.
3. **Don't get blocked on data access.** Legal review on historical data would have stalled us for weeks. A synthetic dataset grounded in floor observations unblocked engineering *and* gave us exact ground truth for evals.
4. **Build against the client's real friction.** Webhooks that arrive late or twice, string-typed fields, state codes. If the mock is too clean, production will hurt.
5. **Category isn't risk.** "Access request" to Outlook and "access request" to payroll look identical in text. Risk has to be keyed on the target application too, which came out of one 30-minute conversation with an app owner.

**What I'd do differently**
- _(add your own reflections here as you work through it)_

---

## Phase 2: Intake (redaction and classification)

**What was built**
- `TicketingAdapter` + ServiceNow adapter: retries with backoff and jitter, typed `Incident`, read-after-write check for silently ignored fields
- Platform webhook receiver: HMAC verification, idempotency ledger, loop protection, 202-then-process
- Context-aware PII redactor with consistent tokens and an in-memory vault
- Keyword baseline and Claude Haiku 5.5 classifier (forced tool use, fail-safe to `unknown`)
- Golden sets: `golden_v1` (300 synthetic, stratified) and `hard_v1` (45 hand-written)
- Eval harness: risk-downgrade errors, calibration, accuracy by style, latency and cost
- First end-to-end flow: ticket created → webhook → redact → classify → work note back in the client system

**FDE lessons**
1. **Your own writes are events too.** Writing a work note back to the ticket fires `incident.updated`, which comes straight back to our webhook. Without an explicit "intake handles `incident.created` only" rule, the platform would loop on its own notes. Integration bugs live in these round trips.
2. **The first baseline run scored 85.7% for a silly reason.** The keyword rule `claim` matched every `DISCLAIMER` email footer, so a third of misclassifications were "expense claim". One word boundary took it to 94.7%. Always read the confusion table and the actual failing tickets before believing (or dismissing) a number.
3. **Synthetic evals flatter whoever wrote the generator.** Keyword rules scored 94.7% on synthetic tickets and **26.7% on 45 hand-written ones**. Redaction scored 100% on synthetic data, which says more about the generator than the redactor. The fix isn't to stop using synthetic data. It's to always keep a second set written by someone (or something) else, and to report both.
4. **Accuracy is the wrong headline for an operating model.** Fourteen risk-downgrade errors (phishing read as a password reset, payroll access read as an ordinary app access) matter more than the 5% overall error rate. The metric has to match the harm.
5. **Separate the client's data from yours, even locally.** Both services sharing one SQLite file raced on table creation the first time they started together. Each service now owns its own tables (`client_*` / `hao_*`) and its own database URL, as they would in production.
6. **Fail safe, not fail silent.** Every classifier failure (API error, invalid enum, refusal) becomes `unknown` at low confidence. The policy engine will send that to a human, so a model outage degrades to the old manual process instead of to wrong automation.

**What I'd do differently**
- _(add your own reflections here, e.g. after running the Claude eval)_

---

## Phase 3: Policy engine + audit log
_(to be written)_
