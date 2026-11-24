# ADR 0010: Autonomy is earned from human-verified evidence, lost automatically, and promoted only by owners

**Status:** Accepted · **Date:** Phase 5

## Context
The design promised that categories "earn autonomy as accuracy is proven, and lose it automatically when quality drops" (problem statement). Phase 4 produces the raw signals: review verdicts, QA verdicts, and reopens arriving as `incident.updated` webhooks. This ADR decides how those signals turn into level changes, and who is allowed to make each kind of change.

## Decision

**What counts as evidence** (per work item, over a rolling 14-day window):

| Signal | Source | Counts as |
|---|---|---|
| Draft approved as-is | review console | correct |
| Draft approved with a *minor* edit (`tone`) | review console | correct. The action was right; only the wording changed |
| Draft edited for any other reason, or rejected | review console | wrong |
| QA sample passed / failed | review console | correct / wrong |
| User reopened an AI-resolved ticket within 72 h | client webhook | wrong, even if a human had approved it |

- An item with *any* negative signal is wrong.
- **Unsampled A3 work that nobody reopened is not counted.** Silence isn't evidence.
- Only evidence gathered **since the category's level last changed** counts. One bad week can't demote twice, and A2-era approvals can't vouch for A3 behaviour.

- **Precision uses only items a human verified**: every A2 review, and the *random* QA sample of A3 work. Reopens of unsampled A3 work are measured separately, as a **reopen rate over all A3 executions**.

**Demotion: automatic, immediate, no deploy.** The controller writes a row in `hao_autonomy_overrides`. The policy engine applies it as one more cap (step 5 in ADR 0006), so it can only lower a level. A demotion is triggered by:
- a single **`unsafe`** verdict, at any level, checked the moment it is recorded
- **at A3** (A3 → A2): QA-sample precision < 90% over ≥ 10 samples, or a reopen rate > 10% over ≥ 20 executions. Checked after each negative signal and nightly

**Promotion: proposed by the controller, approved by the owner.** A proposal requires ≥ 50 items at ≥ 95% precision, no active demotion, and the category below its `max_level`. It records the exact change (`categories.password_reset.current_level: A2 → A3`) and the evidence. `hao controller apply <id> --approved-by <owner email>` only works for the **named category owner**. It edits `autonomy.yaml` in place (comments preserved) for the policy PR, and the change goes live with `POST /policy/reload` after merge. Lifting a demotion (`hao controller clear`) is also owner-only, and evidence restarts from zero afterwards.

## What the simulated pilot caught (twice)
`hao pilot simulate` runs weeks of synthetic traffic through the real system with simulated reviewers and users. It found two design bugs that no unit test had.

### 1. Precision demotions at A2 had no way back
The first version demoted any level on low precision. In the first simulated pilot, `password_reset` was demoted **A2 → A1 on day 2**, after 16 items with 2 negatives: one real misclassification and one random user reopen. It **never recovered**. At A1 the AI only writes handoff packets, so there are no drafts to review and no evidence can accumulate. The controller had locked a category out on one noisy day.

The fix: **demotion removes autonomy, not oversight.** At A2 a human already approves every action, so low precision there just blocks promotion. Demoting A2 → A1 would remove the human-reviewed path *and* the only route back. `unsafe` is the exception, because it says the drafts themselves are dangerous to put in front of people.

### 2. Sampling bias demoted healthy A3 categories
With that fixed, the next 28-day run promoted `password_reset` to A3 on day 5 and **demoted it again on day 7**: "precision 85.7% over 14 items". `account_unlock` went the same way. The cause was the denominator. At A3 only a 20% random sample is QA-reviewed, but users can reopen *any* execution. So the evidence set was "QA'd items (mostly passes)" plus "every reopened item (all failures)": negatives drawn from 100% of the work against positives from 20% of it. Example: 100 executions, 20 QA'd and all passing, and 5 reopens among the other 80. The true error rate is ~5%, but it reads as 20/25 = 80% precision.

The fix: precision only from verified items, and reopens as a rate over *all* executions, each with its own threshold. `tests/test_controller.py::test_reopens_of_unsampled_work_do_not_bias_precision` pins it down with exactly that example.

## Consequences
- ✅ The asymmetry matches the risk: getting less autonomy is fast and automatic, getting more is slow and signed by a named person.
- ✅ Every change (demotion, proposal, approval, clearing) is an audit event with its evidence.
- ⚠️ Thresholds on small samples are noisy: 10 items at 90% means one error is the difference. A Wilson lower bound would be statistically cleaner. We kept plain thresholds so owners can reason about them, and revisit after real pilot data.
- ⚠️ At low volume, 50 items in 14 days takes a while for smaller categories (how-to is ~6% of tickets). That is intended: autonomy should be slow to earn where evidence is thin.

## Result after both fixes
The same 28-day simulation (5,600 tickets) promoted `password_reset` (day 5), `account_unlock` (day 7) and `how_to_question` (day 14), with **no flapping**. A3 work had a 3.5% reopen rate and 86/86 QA passes. See the [simulated pilot readout](../pilot-readout-simulated.md).
