# ADR 0003 — Build on a synthetic dataset while historical data is under legal review

**Status:** Accepted · **Date:** Week 1

## Context
Legal approval to use 12 months of historical tickets is pending. Engineering and evaluation can't wait 3–4 weeks.

## Decision
Generate a **deterministic, seeded synthetic dataset** that reproduces what we observed on the service desk floor:

- Category mix and ticket volume shape (Monday and month-end peaks)
- Missing and wrong user-selected categories ("Other" dominates)
- Tanglish/Hinglish, terse and angry phrasings, forwarded email chains, HTML remnants
- Signatures containing PII (phone numbers, employee IDs, emails), to exercise redaction
- Near-duplicate tickets from the same caller

Tickets the client system "sees" (`tickets.jsonl`) are kept **separate from ground-truth labels** (`labels.jsonl`). That way evaluation never leaks into the system under test.

Template-based generation was chosen over LLM-generated text for Phase 1 because it is reproducible, free, and gives exact ground truth. LLM paraphrasing for extra diversity is a Phase 2 option.

## Consequences
- ✅ Evals are reproducible (`--seed 42`).
- ⚠️ Synthetic text is less varied than reality. Once legal approves, re-run evals on a redacted historical sample before any autonomy promotion.
