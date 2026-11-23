# ADR 0008: Agent lanes as a small DB-backed state machine; the LLM writes words, playbooks pick actions

**Status:** Accepted (supersedes the "LangGraph" line in the original stack plan) · **Date:** Phase 4

## Context
Once the policy engine picks a level, something has to *do* the work: route, draft, wait for a human, act. The original plan was LangGraph, because it has human-in-the-loop interrupts and checkpoints. Two things came up when we looked at what Kaveri would actually have to run:

1. **The state that matters is already ours.** An A2 approval can sit for hours, across restarts and deploys. It has to survive in a table that audit, the review console and the leadership dashboard can all query. LangGraph would keep a second copy of that state in its own checkpointer format.
2. **The flow is short and fixed:** route → plan → draft → guard → (wait) → execute → sample. There is no open-ended agent loop to orchestrate.

## Decision
- **Lanes are plain functions over a `hao_work_items` table** with explicit states: `human_only`, `handed_off`, `awaiting_approval`, `executed`, `rejected`, `failed`, `qa_pending`, `qa_done`. Every transition is an audit event.
- **The LLM writes words; the playbook picks actions.** `policy/playbooks.yaml` (owned by the category owner) lists the exact tools for each category. Drafters (template or Claude Sonnet 5.5) only produce the customer reply, close notes and handoff summary. **No LLM output can add, remove or change an action.**
- **No playbook, no autonomy.** A category promoted to A3 without a playbook still gets only the A1 handoff.
- **Defence in depth on every action**, checked at execution time and not just at planning:
  1. Policy level: A3 for the agent; A2 only with a named human approver.
  2. Tool allowlist from the playbook.
  3. Our tool guard: identity actions on FinCore/PeopleDesk are refused for AI actors.
  4. **The client's own IAM refuses them too** (`403 Policy violation`). We don't rely on our side alone.
- **Draft guardrails** (`check_draft`) run before anything can reach a customer:
  - no raw PII
  - no PII tokens that weren't in the ticket (hallucinated people)
  - nothing that looks like a credential
  - no links outside `kaveri.example`
  - a length limit
- **PII stays inside the boundary.** Plans store `{"application": "SSO"}`, never an email address. The caller's email is read live from the client system at execution time. Drafts are written with tokens (`Hi <PERSON_1>`) and revealed only when posted. Redaction is deterministic, so the same tokens can be rebuilt at approval time without storing a vault.
- **Fail safe.** Each of these degrades to the A1 handoff packet and never to more autonomy: unusable plan (no application, software not in the catalogue), a guardrail violation, or a tool error.
- **Retrieval is BM25** over the client's KB and past triaged tickets. It's pure Python, explainable, and adequate at a few thousand documents. pgvector and embeddings are the upgrade once real tickets show where lexical search misses (likely Tanglish paraphrases).

## Consequences
- ✅ One source of truth for work state. Anything the console or audit shows is the real state.
- ✅ A reviewer can see exactly which actions will run *before* approving, because they're fixed by the playbook.
- ✅ No new framework for Kaveri's team to learn or upgrade.
- ⚠️ If future work needs open-ended, multi-step agent reasoning (e.g. diagnosing app errors from logs), revisit a graph framework *for that lane only*. Keep the work-item table as the system of record.
- ⚠️ Tools are synchronous and sequential. If a mid-plan failure leaves partial effects (e.g. password reset but no reply posted), the ticket is handed off with the full results list, so the human sees exactly what already happened.
