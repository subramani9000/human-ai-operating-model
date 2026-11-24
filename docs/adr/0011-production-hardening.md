# ADR 0011: Production hardening for the pilot go-live

**Status:** Accepted · **Date:** Phase 6

## Context
Phases 1–5 built the operating model. Before Kaveri lets it touch real tickets, the open risks recorded along the way have to be closed:
- typed reviewer identities (ADR 0009)
- drafts that can wait forever (ADR 0009)
- no way to stop everything at once
- webhook replay and secret rotation
- no guard against a change that quietly makes the system worse
- nothing for the operations team to monitor

## Decisions

**1. SSO through an identity-aware proxy, not a login system of our own.** oauth2-proxy sits in front of the platform (compose profile `sso`), authenticates against Kaveri's IdP and forwards the user's email.
- With `AUTH_MODE=proxy` the platform trusts that header only when the request also carries the proxy's shared secret, or when the platform is reachable only through the proxy.
- It requires an `@kaveri.example` account. Admin endpoints (controller run, policy reload, sweep) require an email on `ADMIN_EMAILS`.
- **The reviewer identity comes from the sign-in; a `reviewer` field in a request is ignored** (tested with a forged field).
- `/health`, `/webhooks/ticketing` (HMAC-signed) and `/metrics/prometheus` (aggregate counts only, internal network) stay outside SSO.
- *Why not build auth in:* Kaveri already runs an IdP and MFA. A proxy is a standard, separately patched component their IT team knows. Our code just has to refuse unproven identities.

**2. A kill switch with asymmetric controls.**
- **Any named owner** can switch it on (`hao killswitch on --by … --reason …`, or `POST /killswitch`). It writes a global runtime cap (A1 by default, A0 optional) through the same override mechanism as demotions, so the engine applies it to every decision immediately and pending A2 approvals can no longer execute.
- **Only the sponsor** can switch it off. Stopping must be easy; restarting is a decision someone accountable signs.
- Both actions are audit events, and the console and dashboard show a banner while it's on.

**3. Unreviewed drafts expire to a human.** An A2 draft not reviewed within `approval_timeout_hours` (4 h, in `playbooks.yaml`) becomes `expired`. The ticket gets a work note telling a human to pick it up, and nothing is executed. `hao platform sweep` / `POST /sweep` runs every 15 minutes. Expired drafts aren't counted as feedback (nobody judged them), but they appear on the dashboard as a reviewer-capacity signal.

**4. Webhook replay protection and zero-downtime secret rotation.**
- Deliveries whose `sent_at` is more than `WEBHOOK_MAX_AGE_S` (300 s) from now are rejected. Together with the delivery-id ledger, this stops replays.
- During rotation the platform accepts both `WEBHOOK_SECRET` and `WEBHOOK_SECRET_PREVIOUS`. Order: set the previous secret, switch the client to the new one, then clear the previous.

**5. An eval gate in CI.** `hao eval gate` measures redaction recall and precision, classifier accuracy, macro-F1 and risk-downgrade errors on both golden sets, and high-risk over-autonomy in the policy simulation. It compares them with `evals/baselines.yaml`:
- quality metrics may drop by at most 0.005
- counts of bad outcomes may never increase
- improving a baseline is an explicit `--update` in the PR that earns it

*Proof it works:* deleting the security tripwire from `autonomy.yaml` fails the gate, `keyword/golden_v1.over_autonomy_high_risk 1 → 2 FAIL`, even though every unit test still passes.

**6. Observability without new infrastructure.** Structured JSON logs carry the correlation ids (`sys_id`, `number`, `delivery_id`): one `intake.done` line per ticket with category, autonomy level, lane outcome and latency, plus webhook rejections. Logs never contain ticket text or personal data (tested). `/metrics/prometheus` exposes decisions by level, work items by state, webhook outcomes, kill-switch state, active demotions, classifier tokens and audit-chain integrity.
- *Langfuse deferred:* LLM traces matter once the Claude classifier and drafter are switched on in production. Until then, token counts and latencies are already recorded per ticket, and Langfuse would be one more service for Kaveri to run with nothing to trace.

## Consequences
- ✅ Every open risk from ADRs 0006–0010 is closed or has an owner (see [handover](../handover.md)).
- ⚠️ Prometheus metrics are computed from the database on each scrape. Fine at pilot volume (~600 tickets/day); move to incremental counters before scaling beyond Kaveri's service desk.
- ⚠️ The shared-secret header between proxy and platform needs proper secret management (vault or k8s secret) in production. The `.env` file is for local use only.
