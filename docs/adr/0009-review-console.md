# ADR 0009: A plain review console, and approvals that re-check the policy

**Status:** Accepted · **Date:** Phase 4

## Context
A2 is where humans and AI actually share the work, so the review step decides whether the operating model is trusted. From discovery: agents must be able to override without penalty (D7), L1 agents don't want to be blamed for AI mistakes (D2), and audit wants to know who approved what (D4).

## Decision
- **Server-rendered HTML served by the platform** (`/console`). No front-end build, JavaScript framework or extra service. Streamlit was considered and rejected: it is another runtime for Kaveri to host, and harder to test. The console is covered by the same in-process tests as the API.
- **One page per draft, everything needed to decide:**
  - the **original** ticket, read live from the client system (reviewers are Kaveri staff who can already see it)
  - why the policy picked this level (every step)
  - the exact planned actions
  - the KB and similar-ticket evidence
  - the draft reply, editable in place
- **Verdicts:** approve · edit (reason required) · reject (reason required). For QA samples of A3 work: pass · fail (reason required). Reason codes come from `playbooks.yaml` so they stay consistent and countable. **Every verdict is stored as feedback**, the input to Phase 5's autonomy controller.
- **Approvals run under the approver's identity** (`actor = user:<email>`) in the client system and in the audit log. The human who approved is the one on record.
- **Approval re-checks the policy at the moment of approval.** If the category was demoted, or a tripwire added, after the draft was made and the ticket would no longer be A2, the approval is refused.
- Human-written edits are still checked for credentials and foreign links. They are *not* checked for personal data, because a human replying to a colleague by name is normal.

## Consequences
- ✅ Reviewer, timestamp, verdict and reason are on every human decision, which gives Phase 5 the evidence it needs.
- ✅ Overrides are first-class data rather than friction, as HR and the employee representative asked.
- ⚠️ **No authentication yet**: the reviewer types an email. Go-live needs SSO in front of `/console` (Kaveri's IdP) so the reviewer identity is asserted, not typed. This is the top Phase 6 hardening item.
- ⚠️ No queue assignment or SLA timers on pending drafts yet. A draft left unreviewed just waits. Add an "unreviewed after N hours → hand off to a human" sweep in Phase 6.
