# ADR 0006: Policy engine steps can only lower autonomy, and tripwires don't trust the classifier

**Status:** Accepted · **Date:** Phase 3

## Context
The policy file has several kinds of control: category levels, application caps, risk-tier caps and rules. If their combined effect depends on evaluation order, or if any step can *raise* a level, then nobody (auditors included) can reason about the policy by reading it. Phase 2 also showed that the classifier is sometimes **confidently wrong**. A phishing report read as a password reset at 0.95 confidence slips past every confidence-based rule.

## Decision
1. **Fixed order, monotone steps.** Start at the category's `current_level`, then apply the application cap, then the risk-tier caps, then the rules in file order, then clamp to `max_level`. **Every step keeps or lowers the level; none raises it.** So any cap in the file holds whatever else is configured.
2. **Unknown work goes to a human.** An unrecognised category resolves to `unknown_category` (validated to be A0) with a named owner (the L1 lead).
3. **Every decision explains itself.** The decision records each step (`from`, `to`, reason), the policy content hash and the human-readable label. The work note says it in one line: `A0 (Human only). rule low-confidence-downgrade → A0.`
4. **Tripwire rules** (`text_matches`) look at the redacted ticket text directly, independently of the classifier: security language (phish, clicked, gift card, asking for password…) and outage language cap the ticket at A0. Over-triggering costs automation, never safety.
5. **Property-based tests** (Hypothesis) check the invariants on the real policy **and on the most permissive policy the validator allows**, with every category promoted to its `max_level`. The invariants: no step raises the level; FinCore/PeopleDesk work never exceeds A2; critical work and P1 tickets are A0; VIP and reopened tickets are at most A1; confidence < 0.5 means A0; lower confidence never gives more autonomy.

## Evidence: `hao policy simulate`
Each golden ticket is decided twice, once with the classifier's output and once with the true label (the **intended** level). *Over-autonomy* (AI got more than intended) is the dangerous direction.

| Keyword classifier | Over-autonomy | …on high/critical work | Under-autonomy |
|---|---|---|---|
| `golden_v1`, rules off | 8 | 3 | 1 |
| `golden_v1`, current policy | **1** | **1** | 129 (43%) |
| `hard_v1`, rules off | 6 | 4 | 19 |
| `hard_v1`, current policy | **4** | **2** | 24 |

What this tells us:
- **The rules work, but the keyword classifier makes them expensive.** Its confidence is badly calibrated: most correct answers sit between 0.5 and 0.8, so the low-confidence rule sends 43% of synthetic tickets to a lower level than intended. A better-calibrated classifier is how automation gets recovered. That's the case Phase 2's Claude eval needs to make.
- **Tripwires caught every phishing and outage ticket in `golden_v1`.** On `hard_v1` they missed two phishing reports worded in ways the patterns don't cover ("mailbox will be deleted unless I log in via their page"). **We deliberately did not tune the tripwires on `hard_v1`**: it's the held-out set, and fitting rules to it would make it worthless as a test. The remaining risk is recorded below.
- Promoting password reset, account unlock and how-to questions to A3 adds no over-autonomy on either set. That's the first piece of evidence for a Phase 5 promotion, but not sufficient on its own.

## Consequences
- ✅ Anyone can read the YAML and know the worst case. Audit can replay any decision from its recorded steps and policy version.
- ⚠️ **Open risk:** confidently misclassified phishing that avoids the tripwire vocabulary. Mitigations: (a) the Claude classifier on `hard_v1`, (b) grow tripwire patterns from **real** reviewed tickets (never from `hard_v1`), (c) consider a dedicated security classifier as a second opinion.
- ⚠️ Rules use AND semantics within a rule and are evaluated in order. Keep them few and simple.
