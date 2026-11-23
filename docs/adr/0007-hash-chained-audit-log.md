# ADR 0007: Hash-chained, append-only audit log with an external anchor

**Status:** Accepted · **Date:** Phase 3

## Context
Internal audit (discovery D4) needs, for every automated action: what was decided, by what logic, on what data, approved by whom and when. It must be retrievable for 7 years and it must be evident if anyone edits the log. "The model decided" is not acceptable. Every entry needs a policy reference.

## Options
1. Plain application log table. It can be edited silently.
2. Database triggers that block `UPDATE`/`DELETE`. A DBA can disable them, and it says nothing about what happened before.
3. A managed ledger database or WORM storage (e.g. QLDB-style, S3 Object Lock). The right end state, but a procurement and infrastructure dependency for an 8-week pilot.
4. **Hash chain in the platform DB, with the head hash anchored outside it.**

## Decision
Option 4, designed so it can move to option 3 later without changing the event model.

- Every event stores `hash = sha256(prev_hash | canonical_json(ts, incident, actor, action, payload, policy_version, decision_id))`. The first event links to a genesis hash of zeros.
- `prev_hash` is **unique**, so two concurrent writers can't both extend the same head. The loser gets an integrity error and retries on the new head. Tested with four threads writing at once.
- Events for one ticket: `intake.redacted` (PII counts only) → `intake.classified` (category, confidence, model, prompt version) → `autonomy.decided` (level, every step, owner, policy label) → `ticket.annotated`. `policy.loaded` is recorded whenever the live policy version changes.
- **Payloads never contain raw PII.** Redacted text and counts only.
- `hao audit verify` walks the chain and reports the first broken event: edited content, or a deleted/inserted/reordered row.
- **Anchoring:** a hash chain is tamper-*evident*, not tamper-*proof*. Someone with write access could rebuild the entire chain. So `hao audit head` is shipped **outside the database** daily (to audit's mailbox or WORM storage), and `hao audit verify --expected-head <hash>` fails if that anchored hash is no longer in the chain.

## Evidence
- `tests/test_audit.py`: detects an edited payload (event 3), a deleted row, and a full chain rewrite (via the anchor). Survives four concurrent writers without forking.
- Live run: editing `level` to `A3` directly in SQLite → `Audit chain BROKEN at event 4: content does not match its hash (row edited)`, exit code 1.
- `GET /decisions/{sys_id}` returns the decision and its full audit trail, the view Sanjay (audit) asked for.

## Consequences
- ✅ Meets the pilot's "100% of AI actions have a complete audit record" metric, verifiably.
- ⚠️ Single global chain means one writer at a time. That's fine at ~600 tickets/day. Shard per day or per category if throughput ever matters.
- ⚠️ 7-year retention needs an export and archive job (`hao audit export`). Partitioning is a Phase 6 item.
