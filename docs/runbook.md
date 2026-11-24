# Runbook: operating the Human+AI platform at Kaveri

For the IT Ops on-call engineer and the category owners. Every command here is a real `hao` command.

## Daily and scheduled jobs

| When | Job | Command | Owner |
|---|---|---|---|
| Every 15 min | Expire unreviewed A2 drafts to a human | `hao platform sweep` (or `POST /sweep`) | Platform (scheduler) |
| Nightly 02:00 | Autonomy controller: demotion checks + promotion proposals | `hao controller run` (or `POST /controller/run`) | Platform (scheduler) |
| Daily 06:00 | Anchor the audit chain outside the database | `hao audit head >> /secure/anchors/$(date +%F).txt` (WORM storage or audit's mailbox) | Platform (scheduler) |
| Weekly | Verify the audit chain against the last anchor | `hao audit verify --expected-head <hash from last anchor>` | Internal audit (Sanjay Mehta) |
| Weekly | Review proposals and demotions | `hao controller status` | Category owners |

## Health checks

| Signal | Where | Healthy | Act if |
|---|---|---|---|
| Service up | `GET /health` | 200 with the policy label | not 200 for 2 min → page on-call |
| Audit integrity | `hao_audit_chain_ok` | 1 | 0 → **incident**: see "Audit chain broken" |
| Webhooks | `hao_webhook_events_total{status="failed"}` | flat | rising → check the client system and the platform logs (`event=webhook.rejected`) |
| Review backlog | `hao_work_items{state="awaiting_approval"}` and `{state="expired"}` | expired ≈ 0 | expired > 5/day → reviewer capacity issue; tell Priya Nair (L1 lead) |
| Kill switch | `hao_kill_switch_on` | 0 | 1 → intentional; confirm with whoever pulled it (audit log) |
| Demotions | `hao_active_demotions` | 0 | > 0 → owner must investigate (see below) |

Logs are JSON lines with `sys_id`, `number` and `delivery_id`. To follow one ticket end to end, filter by `number=INC00xxxxx`.

## Playbook: "the AI did something wrong"

1. **Stop the line if it could happen again.** Any category owner:
   `hao killswitch on --by you@kaveri.example --reason "INC0012345: wrong password reset"`.
   Every category drops to A1 (AI still triages and writes handoff packets; executes nothing) and pending approvals can no longer run. Use `--cap A0` to stop AI involvement completely.
2. **Reconstruct what happened:** `GET /decisions/<sys_id>` shows the decision, every policy step behind it, and the full audit trail (who approved, which tools ran, what they returned). `hao audit export --incident <sys_id>` for the file to send to audit.
3. **Fix the cause:**
   - wrong category → classifier or taxonomy change, which must pass `hao eval gate`
   - wrong action → playbook change, owner-approved PR
   - missing safeguard → a policy rule (e.g. a new tripwire). Add the case to `tests/` and **never** to `data/golden/hard_v1.jsonl` (ADR 0006).
4. **Restart:** only the sponsor (Ramesh Iyer): `hao killswitch off --by ramesh.iyer@kaveri.example --note "fixed in PR #…"`.

## Playbook: a category was demoted

The controller demoted it automatically: an `unsafe` verdict, or A3 quality below threshold. `hao controller status` shows why.
1. The owner reviews the evidence: the reasons and verdicts in `hao controller status`, and the work items in the console.
2. Fix the cause (as above).
3. Owner lifts it: `hao controller clear password_reset --by priya.nair@kaveri.example --note "…"`. Evidence starts again from zero, so the category has to re-earn any promotion.

## Playbook: approve a promotion

1. The nightly run proposes it (`hao controller run` prints `PROPOSAL #n …`, and the dashboard shows it).
2. The owner reviews the evidence, plus the Claude eval on `hard_v1` for that category (ADR 0005).
3. The owner applies it: `hao controller apply <n> --approved-by owner@kaveri.example`. This edits `policy/autonomy.yaml`.
4. Open a PR with that file. CI runs `hao policy validate` and `hao eval gate`.
5. After merge and deploy: `POST /policy/reload`. The `policy.loaded` audit event records the new version.

**Rollback:** revert the PR and reload. For immediate effect before the revert lands, the owner can pull the kill switch, or wait for the controller to demote on evidence.

## Playbook: rotate the webhook secret (no downtime)

1. Platform: set `WEBHOOK_SECRET_PREVIOUS=<current>` and `WEBHOOK_SECRET=<new>`, then restart. Both are now accepted.
2. Client ticketing system: switch its signing secret to `<new>`.
3. Confirm deliveries succeed (`hao_webhook_events_total` rising, no `webhook.rejected` logs).
4. Platform: clear `WEBHOOK_SECRET_PREVIOUS`, then restart.

## Playbook: client ticketing system is down or slow

- **Our writes** (work notes, actions): the adapter retries 4× with backoff, then fails safe. Failed actions put the work item in `failed` and hand off to a human, and annotation failures are recorded (`work_note_status=failed`). Nothing is lost from the audit log.
- **Their webhooks**: they retry on their side. Deliveries older than 5 minutes are rejected as stale, so after a long outage **re-sync missed tickets** by listing new incidents (`GET /api/now/table/incident?sysparm_query=state=1^ORDERBYopened_at`) and replaying them through intake. ⚠️ An automated re-sync job is an open item (see handover).

## Playbook: audit chain broken

`hao audit verify` reports the first broken event id and why: edited content, or a deleted, inserted or reordered row.
1. **Treat as a security incident.** Tell the CISO office (Farah Khan) and internal audit (Sanjay Mehta).
2. Pull the kill switch (A0).
3. Preserve the database as-is (snapshot) before anyone touches it.
4. Compare with the last anchored head: `hao audit verify --expected-head <anchor>` tells you if the chain was rewritten wholesale.
