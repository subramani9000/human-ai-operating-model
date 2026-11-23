# ADR 0005: Choose the classifier with evals, including one set the generator did not write

**Status:** Accepted · **Date:** Phase 2

## Context
The autonomy policy (Phase 3) acts on the classifier's output: category, application and confidence. A wrong category on a payroll ticket can route high-risk work into a low-risk lane. So we need to know **how** the classifier fails, not just how often. We also need to know whether an LLM is worth its cost over the keyword rules the service desk already has.

## Decision
1. **Two classifiers behind one interface.** `KeywordClassifier` is the free, instant baseline. `ClaudeClassifier` uses Claude Haiku 5.5 with **forced tool use** for structured output (enum-constrained category and application, numeric confidence).
2. **Two evaluation sets:**
   - `golden_v1`: 300 synthetic tickets, stratified 20 per category and round-robin across phrasing styles.
   - `hard_v1`: 45 **hand-written** tickets the generator never produced: paraphrases, vague one-liners, multi-issue tickets, misleading user categories, and a **prompt-injection** attempt.
3. **Metrics that matter for the operating model**, beyond accuracy:
   - **Risk-downgrade errors**: the true category's risk tier is higher than the predicted one. These are the dangerous errors.
   - **Calibration (ECE):** the policy engine downgrades autonomy below 0.80 confidence, so confidence must mean something.
   - **Accuracy by phrasing style**: Tanglish, Hinglish, terse and angry tickets.
   - Latency, tokens and cost per ticket.
4. **Fail safe.** Any API error, invalid enum or refusal becomes `unknown` with confidence ≤ 0.3. Under the policy rules that lands in a human lane (A0).
5. **The ticket is untrusted input.** The system prompt says so, the ticket is wrapped in `<ticket>` tags, caller metadata is never sent, and `hard_v1` includes an injection attempt.

## Evidence so far (keyword baseline)

| Set | Accuracy | Macro F1 | Risk-downgrade errors | ECE |
|---|---|---|---|---|
| `golden_v1` (synthetic) | 94.7% | 0.947 | 14 | 0.175 |
| `hard_v1` (hand-written) | **26.7%** | 0.303 | 5 | 0.205 |

The 68-point drop is the main finding of Phase 2. **Synthetic data drawn from a fixed set of templates flatters a pattern-matcher.** On `golden_v1` alone, keyword rules look good enough to ship. On text they weren't written for, they fail: they return `unknown` 44% of the time, and when they do answer at high confidence they are right only about half the time.

The Claude classifier's numbers will be added when it is run against both sets (`hao eval classify --classifier claude --golden hard_v1`). Promotion of any category beyond A2 in Phase 5 requires its results on `hard_v1`, not just `golden_v1`.

## Consequences
- ✅ "Is the LLM worth it?" gets answered with numbers on the same inputs, including cost per ticket.
- ✅ Prompt and taxonomy changes are regression-tested: `policy/taxonomy.yaml` descriptions are part of the prompt.
- ⚠️ `hard_v1` is small (45 tickets), so per-category numbers are noisy. Grow it from real tickets the human reviewers correct in Phase 4.
