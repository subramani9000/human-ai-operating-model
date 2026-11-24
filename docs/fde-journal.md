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

**What was built**
- Policy engine with a fixed, monotone evaluation order and a per-step explanation trail
- Tripwire rules on the ticket text that don't depend on the classifier
- Property-based tests (Hypothesis) on the real policy and on the most permissive policy the validator allows
- Hash-chained audit log: fork prevention, `verify`, `head` anchoring, `export`
- Decisions wired into intake: every ticket gets a level, an accountable owner and a work note explaining why
- `hao policy simulate`: production-vs-intended comparison, rule ablation, and what-if promotions

**FDE lessons**
1. **Make the policy readable by the people who'll be blamed for it.** "Every step can only lower the level" is one sentence an auditor can check against the YAML. Clever rule interactions would have been more flexible and impossible to sign off.
2. **Test invariants against the worst *allowed* configuration, not today's.** Today no category is at A3, so "payroll never reaches A3" is trivially true. Running the property tests against a policy with everything promoted to its ceiling is what makes the guarantee survive Phase 5.
3. **Safety rules have a price, and the classifier's calibration sets it.** The low-confidence rule cut over-autonomy from 8 to 1, but also sent 43% of tickets to a lower level than intended, because the keyword classifier's confidence means little. That turns "is the LLM worth it?" into a concrete question: does better calibration buy back that 43%?
4. **Confident errors need a different defence.** Confidence rules can't catch a model that is wrong *and* sure. Deterministic tripwires on the raw text, independent of the classifier, catch those cases. Defence in depth, not one clever check.
5. **Protect the held-out set from yourself.** The tripwires missed two phishing tickets in `hard_v1`. Adding those phrasings would have fixed the number and destroyed the test. The miss is recorded as an open risk in ADR 0006 instead.
6. **Tamper-evident is not tamper-proof. Say which one you built.** A hash chain only proves integrity if its head is anchored somewhere the attacker can't reach. Being precise about this in the ADR builds more trust with audit than overclaiming.

**What I'd do differently**
- _(add your own reflections here)_

---

## Phase 4: Agent lanes

**What was built**
- Lanes A0–A3 over a durable `hao_work_items` state machine: route, plan, draft, guard, wait, execute, QA-sample
- `policy/playbooks.yaml`: per-category tool allowlists; "no playbook, no autonomy"
- Guarded tools, checked at execution time: level, allowlist, privileged-app guard. The mock's IAM refuses AI actors on HR/finance apps too
- Template and Claude Sonnet 5.5 drafters; `check_draft` guardrails for PII, invented tokens, credentials and links
- BM25 retrieval over the client KB and past tickets for handoff packets
- Review console: approve / edit / reject with reason codes, re-checked against the live policy, executed under the approver's name
- First full loop on the live services: ticket → A2 draft → approve in the console → password reset by IAM, reply posted, ticket resolved, 12 audit events verified

**FDE lessons**
1. **Drop the framework when the client would only be running your state twice.** LangGraph was in the plan. The flow turned out short and fixed, and the approval state had to live in a table audit and the console already read. A 300-line state machine over that table is easier for Kaveri to run and debug. Changing the plan is fine; writing down why (ADR 0008) is the part that matters.
2. **Let the model write, not decide.** Keeping actions in a reviewed playbook means a reviewer sees exactly what will happen before approving, and prompt injection can at worst produce a bad *sentence*, which the guardrails then check.
3. **Don't be the only lock on the door.** Our guard refuses AI password resets on PeopleDesk, and so does the client's IAM. If our policy, playbook or code is ever wrong, the client's own control still holds. FDEs should push for controls on the client's side, not just their own.
4. **Guardrails produce false positives.** The first credential filter flagged "your temporary password is on its way by SMS" as a leaked password, which would have blocked every password-reset reply. The fix was to match the *shape* of a secret (digits or symbols after "password is"). Every guardrail needs a test for the normal sentence it must *not* block.
5. **State changes while humans think.** A draft can sit for hours, and the category might be demoted in the meantime. Approval re-runs the policy at approval time, so an old draft can't sneak through under an old rule.
6. **PII can stay out of your database if you design for it.** Plans store the application name, not the user's email; the email is read live from the client at execution. Deterministic redaction rebuilds the same tokens at approval time, so the vault never needs to be stored.

**What I'd do differently**
- _(add your own reflections here)_

---

## Phase 5: Learning loop

**What was built**
- Reopen detection from the client's webhooks: idempotent, only for AI-resolved work, inside the 72 h window
- Autonomy controller: per-item evidence since the last level change; automatic demotions as runtime caps; owner-only promotions that edit `autonomy.yaml` for the PR; owner-only clearing
- Policy hot-reload with an audit event
- Leadership dashboard measured against the pilot targets, with a stated-assumption "human hours redirected" estimate
- A shared clock and `hao pilot simulate`: 28 days × 200 tickets/day through the real system, with simulated reviewers, QA, users and owners

**FDE lessons**
1. **Simulate the loop before the client runs it.** Unit tests checked each rule. Only running weeks of traffic through the whole system showed what the rules *do together*. Two design bugs that every unit test passed came out of the first two simulation runs.
2. **Bug 1: a demotion with no way back.** `password_reset` was demoted A2 → A1 on day 2 after one real error and one random reopen in 16 items, and never recovered: at A1 there are no drafts to review, so no evidence can ever accumulate. Fix: demotion removes *autonomy*, not *oversight*. Low precision at A2 blocks promotion instead; only `unsafe` demotes from A2.
3. **Bug 2: sampling bias.** Promoted categories were demoted two days later at "85.7% precision". Positives came from a 20% QA sample, but negatives (reopens) came from 100% of executions. The true error rate was a few percent. Fix: precision from verified items only, reopens as a rate over all executions. **If the denominators don't match, the metric is wrong, however good the code is.**
4. **Make getting less autonomy cheap and getting more autonomy expensive.** Demotion is automatic and needs no deploy. Promotion is a named owner signing a PR. That asymmetry is what lets a cautious sponsor say yes to the pilot at all.
5. **The dashboard must survive a sceptical CFO.** "Human hours redirected" is an estimate, so the assumptions live in a reviewed file and are printed next to the number. A number without its assumptions gets you a meeting about the number.
6. **Let the data say why a target was missed, and who owns the fix.** The simulation missed the 35–40% A3 target (7.9% over the pilot, 10.5% once all promotions landed). The funnel in the readout shows where the rest went, for tickets in the three promoted categories:
   - **39% were held back by policy, not by the AI.** Password resets and unlocks on KLMS and WMS-Pro are capped at A2 because those apps are medium-risk. Whether an identity action on a medium-risk app deserves A3 is a decision for the app owner and the sponsor, not an engineering task.
   - **30% by classifier confidence < 0.80.** That's the calibration problem from Phase 3, which a better classifier can fix.
   - Only 4 categories have playbooks at all, so even a perfect run can't pass ~32% of volume without playbooks for VPN, email and printer.

   "The AI isn't good enough" becomes three concrete workstreams with three different owners.

**What I'd do differently**
- _(add your own reflections here, e.g. after re-running the pilot with `--classifier claude`)_

---

## Phase 6: Hardening and handover
_(to be written)_
