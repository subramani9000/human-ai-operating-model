# Handover: Kaveri Human+AI operating model pilot

What was delivered, how to run it, what's still open, and who owns each part. Read this with the [runbook](runbook.md) and the [threat model](threat-model.md).

## What Kaveri gets

| Capability | Status | Evidence |
|---|---|---|
| Integration with the existing ticketing tool (no replacement) | ✅ | Adapter + signed, idempotent, loop-safe webhooks ([ADR 0002](adr/0002-integrate-not-replace.md)) |
| PII redaction before any LLM call | ✅ | 100% recall on synthetic data, CI gate ([ADR 0004](adr/0004-context-aware-redaction.md)) |
| Classification with harm-weighted evals | ✅ keyword · ⏳ Claude | [Eval reports](eval-reports/) ([ADR 0005](adr/0005-classifier-evaluation.md)) |
| Policy-as-code decision rights with named owners | ✅ | `policy/`, property tests ([ADR 0001](adr/0001-policy-as-code.md), [0006](adr/0006-policy-engine-semantics.md)) |
| Tamper-evident audit of every AI decision and action | ✅ | `hao audit verify` ([ADR 0007](adr/0007-hash-chained-audit-log.md)) |
| Agent lanes A0–A3 with playbooks and double-locked actions | ✅ | [ADR 0008](adr/0008-agent-lanes-and-tools.md) |
| Review console with reason-coded feedback | ✅ | [ADR 0009](adr/0009-review-console.md) |
| Evidence-based autonomy controller | ✅ | [ADR 0010](adr/0010-autonomy-controller.md), [simulated readout](pilot-readout-simulated.md) |
| Leadership dashboard against the pilot targets | ✅ | `/dashboard` |
| SSO, kill switch, approval timeouts, webhook hardening, eval gate, metrics | ✅ | [ADR 0011](adr/0011-production-hardening.md) |

## Against the pilot success metrics (simulated, 28 days × 200 tickets/day)

| Metric | Target | Simulated | Read it as |
|---|---|---|---|
| Tickets at "AI acts" | 35–40% | 7.9% (10.5% after promotions) | **Not met.** Blocked by policy caps on medium-risk apps (39% of eligible tickets), classifier confidence (30%), and playbook coverage (4 categories). See the funnel in the readout |
| Reopen rate on AI-resolved | < 5% | 3.5% | Met |
| Misrouting | < 8% | 1,690 tickets re-routed by AI | Not measured directly. Needs real assignment-change data |
| Median P3 resolution time | −30% | not meaningful | The simulation fixes when reviewers work, so its timings are a modelling choice. Measure against the real 9 h baseline |
| AI actions with a complete audit record | 100% | 100% | Met |
| Human overrides captured as feedback | 100% | 100% | Met |
| Agent trust score | +20 pts | n/a | Run the survey at weeks 0 and 8 |

## Before switching on real traffic

1. **Run the Claude evals** (`hao eval classify --classifier claude` on both golden sets), then `hao policy simulate` and `hao pilot simulate` with `--classifier claude`. Decide keyword vs Claude on `hard_v1` risk-downgrade errors and calibration, not accuracy alone. Owner: FDE team.
2. **Legal sign-off on historical data**, then re-run the redaction and classification evals on a redacted real sample labelled by the CISO team and L1 leads. Owner: Farah Khan (CISO) + FDE team.
3. **HR directory sync** into the redactor (closes the bare-first-name gap). Owner: Lakshmi Sundaram (HR) + IT.
4. **Register the OIDC client** and enable the `sso` profile; make the proxy the only way to reach the platform. Owner: IT security.
5. **Schedule the jobs** in the runbook (sweep, controller, audit anchoring). Owner: IT Ops.
6. **Baseline survey** for agent trust. Owner: Priya Nair + Lakshmi Sundaram.

## Decisions for the sponsor (Ramesh Iyer)

- **Medium-risk application caps.** Password resets and unlocks on KLMS / WMS-Pro are capped at A2 because those apps are medium-risk. That cap is the single biggest reason the A3 target was missed in simulation. Options: keep it (safest), allow identity-only actions at A3 on medium apps (needs a policy change and Vikram Shetty's sign-off), or accept a lower target.
- **Playbooks for VPN, email and printer.** These are low-risk categories with an A3 ceiling but no actions yet, about 20% of volume. Each needs its owner (Joseph Mathew, Divya Krishnan) to agree what the AI may do.

## Open items

| Item | Why it matters | Suggested owner |
|---|---|---|
| Automated re-sync after a long client outage | Stale webhooks are rejected by design, so missed tickets must be pulled | FDE team |
| Langfuse (or equivalent) once Claude is live | Prompt-level traces for debugging drafts | FDE team |
| Wilson-interval thresholds in the controller | Plain thresholds are noisy at small samples ([ADR 0010](adr/0010-autonomy-controller.md)) | FDE team + audit |
| Grow `hard_v1` from reviewed real tickets | 45 tickets is too small for per-category decisions | L1 lead + FDE team |
| Extend the model to HR ops and finance ops | The design is reusable: new taxonomy, owners and playbooks | Sponsor |

## Who owns what after handover

| Area | Owner |
|---|---|
| Policy (`autonomy.yaml`, caps, rules) | Ramesh Iyer (sponsor); category owners per `ownership.yaml` |
| Playbooks per category | The category owner |
| Review console and reviewer rota | Priya Nair (L1), Vikram Shetty (L2) |
| Audit anchoring and verification | Sanjay Mehta (internal audit) |
| Redaction and data handling | Farah Khan (CISO office) |
| Platform operations | IT Ops on-call ([runbook](runbook.md)) |
