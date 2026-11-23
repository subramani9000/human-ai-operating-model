# ADR 0004: Context-aware, deterministic PII redaction before any LLM call

**Status:** Accepted · **Date:** Phase 2

## Context
The CISO office (discovery D5) requires that no raw employee PII reaches an external LLM API, and that redaction is **demonstrable and tested**. Tickets contain names, Indian mobile numbers, employee IDs and personal emails, mostly inside signatures and forwarded headers. They are mangled by typos and HTML remnants.

## Options
1. **Microsoft Presidio with a spaCy NER model.** Strong general-purpose name detection, but it needs a large language model package, is non-deterministic across model versions, and still needs custom recognisers for `KVR-#####` IDs and Indian phone formats.
2. **LLM-based redaction.** Defeats the purpose: the PII would reach a model in order to be removed.
3. **Deterministic, context-aware redactor.** Regexes for structured PII, plus names taken from context: the caller on the ticket, the employee directory, `Name <email>` headers and signature blocks.

## Decision
Option 3 for the pilot, behind a small `Redactor` interface so Presidio can be added as an extra layer later.

- Every person gets one consistent token (`<PERSON_1>`) across fields, so the model can still follow "who said what".
- A reversible **vault** maps tokens back to values. It exists only in memory during intake and is **not persisted** on the platform side. The platform database stores redacted text and per-type counts only. Phase 4's review console will re-read originals from the client system, which already holds them, instead of keeping a second copy.
- Operational identifiers (`WMS-4021`, `ORA-01017`, application names) are deliberately preserved. Over-redaction destroys the signal the classifier needs, so "utility" is measured alongside recall.

## Evidence
`hao eval redaction` on 5,000 synthetic tickets: **100% recall** on 8,714 PII values, **100% precision** (typo-tolerant), **100% utility**. See [eval report](../eval-reports/redaction-synthetic.md).

**These numbers are a ceiling, not a guarantee.** The generator and the redactor were built by the same team, so the dataset contains only the PII shapes we thought of. `tests/test_redaction.py` adds hand-written formats the generator never produces (`98400 12345`, `+91-98400-12345`, PAN, Aadhaar, HTML-split signatures, mangled `Fom:` headers). It also documents a known gap: **a bare first name with no other signal ("ask Suresh in accounts") is only caught when the employee directory is loaded.**

## Consequences
- ✅ Deterministic and fast (< 1 ms per ticket), with no model download and easy-to-explain behaviour for audit.
- ⚠️ Name recall depends on context. **Action for go-live:** nightly sync of the HR employee directory into `Redactor(directory=...)`.
- ↩️ Before production, re-run the redaction eval on a sample of real tickets labelled by the CISO team. Add Presidio as a second pass if recall on real names is below 99%.
