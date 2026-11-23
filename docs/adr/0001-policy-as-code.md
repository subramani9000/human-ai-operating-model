# ADR 0001 — Operating model as policy-as-code (YAML in git)

**Status:** Accepted · **Date:** Week 1

## Context
Audit requires a policy reference behind every automated action. Category owners need to approve autonomy changes. Decision rights currently live in people's heads and in three pilots' hard-coded logic.

## Options
1. Hard-code thresholds in the service.
2. Store policy in a database table with an admin UI.
3. **YAML files in git, validated by a Pydantic schema, loaded at runtime with a content hash as the version.**
4. A full policy engine (OPA/Rego).

## Decision
Option 3. Every decision records the policy's content hash. Changes go through pull requests, so the approval trail comes for free (reviewer = category owner). A CI check rejects invalid policy, e.g. a high-risk category with `max_level: A3`, or a category with no owner.

## Consequences
- ✅ Audit gets diffable, attributable history of every rule change.
- ✅ Non-engineers can read the policy.
- ⚠️ Promotion requires a PR, so it's slower than a toggle. That's intentional.
- ↩️ Revisit OPA if rules outgrow simple conditions (e.g. per-region regulation).
