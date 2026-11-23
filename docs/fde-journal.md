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

## Phase 2 — Intake: redaction and classification
_(to be written)_
