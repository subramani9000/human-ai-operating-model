# Pilot readout (simulated)

> **Simulated, not measured.** 28 days × 200 synthetic tickets/day (seed 99) driven through the real system end to end, with simulated reviewers, QA and users (`hao pilot simulate`). Classifier: `keyword`. Owners approve promotions after 2 days. Reviewers approve correct drafts (editing 5% for tone) and reject wrong ones; users reopen 60% of wrongly resolved tickets and 3% of correct ones.

## Autonomy changes

| Day | Event | Category | Change | Detail |
|---|---|---|---|---|
| 3 | proposed | password_reset | A2 → A3 | 100.0% over 61 items, to Priya Nair |
| 5 | proposed | account_unlock | A2 → A3 | 96.2% over 53 items, to Priya Nair |
| 5 | promoted | password_reset | A2 → A3 | approved by Priya Nair |
| 7 | promoted | account_unlock | A2 → A3 | approved by Priya Nair |
| 12 | proposed | how_to_question | A2 → A3 | 95.5% over 67 items, to Priya Nair |
| 14 | promoted | how_to_question | A2 → A3 | approved by Priya Nair |

Policy at the end vs start: `password_reset` A2 → A3, `account_unlock` A2 → A3, `how_to_question` A2 → A3

## Results over the whole pilot

| Metric | Target | Actual | |
|---|---|---|---|
| Tickets resolved by AI acting alone (A3) | 35–40% (low-risk categories) | 7.9% | ❌ |
| Reopen rate on AI-resolved tickets | < 5% | 3.5% | ✅ |
| AI decisions with a complete audit record | 100% | 100.0% | ✅ |
| Human verdicts captured as feedback | 100% | 100.0% | ✅ |

**5600 tickets**: A3 8% · A2 15% · A1 39% · A0 38%

| Outcome | Count |
|---|---|
| Resolved by AI acting alone (A3) | 442 |
| Drafted by AI, approved by a human (A2) | 855 |
| Drafts rejected | 4 |
| Handed off with a context packet | 2197 |
| Human only (A0) | 2102 |
| Re-routed by AI | 1690 |
| Reopened after AI resolution | 45 (3.5%) |
| QA samples pass / fail | 86 / 0 |
| Override rate on drafts (edited + rejected) | 5.2% |

**Human hours redirected (estimate): 143.6 h**, from 3406 min on AI-resolved tickets and 5472 min on approved drafts, less 258 min of QA and the review time spent on rejected drafts. Assumptions: `policy/value.yaml`.

Audit chain: OK, 38809 events.

## Where A3 was lost (day 15 onwards, after the last promotion)

1040 of 2880 tickets (36%) were classified into the A3 categories (account_unlock, how_to_question, password_reset); 303 (10.5% of all tickets) ran at A3. The first policy step that lowered each ticket's level:

| What kept the ticket below A3 | Tickets | Share of A3-category tickets |
|---|---|---|
| application cap (medium/high-risk app) | 408 | 39% |
| classifier confidence < 0.80 | 314 | 30% |
| nothing: ran at A3 | 303 | 29% |
| VIP requester (capped at A1) | 15 | 1% |

## Day by day

| Day | Tickets | A3 | A2 | A1 | A0 | AI-resolved | Approved drafts | Rejected | Reopened | Hours redirected (est.) |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 325 | 0 | 44 | 159 | 122 | 0 | 44 | 0 | 2 | 4.7 |
| 2 | 274 | 0 | 39 | 138 | 97 | 0 | 39 | 0 | 1 | 4.0 |
| 3 | 207 | 0 | 35 | 99 | 73 | 0 | 35 | 0 | 1 | 3.6 |
| 4 | 232 | 0 | 34 | 121 | 77 | 0 | 34 | 0 | 0 | 3.7 |
| 5 | 205 | 0 | 27 | 98 | 80 | 0 | 27 | 0 | 0 | 2.8 |
| 6 | 61 | 3 | 12 | 19 | 27 | 3 | 12 | 0 | 0 | 1.6 |
| 7 | 46 | 4 | 9 | 15 | 18 | 4 | 9 | 0 | 2 | 1.5 |
| 8 | 290 | 32 | 43 | 112 | 103 | 32 | 43 | 0 | 4 | 8.4 |
| 9 | 255 | 28 | 37 | 95 | 95 | 28 | 36 | 1 | 0 | 6.8 |
| 10 | 245 | 23 | 41 | 87 | 94 | 23 | 41 | 0 | 2 | 6.8 |
| 11 | 217 | 15 | 31 | 93 | 78 | 15 | 31 | 0 | 2 | 5.2 |
| 12 | 252 | 25 | 40 | 94 | 93 | 25 | 39 | 1 | 2 | 7.3 |
| 13 | 53 | 6 | 5 | 25 | 17 | 6 | 5 | 0 | 1 | 1.3 |
| 14 | 58 | 3 | 9 | 19 | 27 | 3 | 9 | 0 | 0 | 1.3 |
| 15 | 341 | 43 | 54 | 122 | 122 | 43 | 54 | 0 | 4 | 11.0 |
| 16 | 225 | 19 | 38 | 83 | 85 | 19 | 37 | 1 | 2 | 6.1 |
| 17 | 239 | 23 | 32 | 90 | 94 | 23 | 32 | 0 | 1 | 6.3 |
| 18 | 222 | 26 | 45 | 70 | 81 | 26 | 45 | 0 | 2 | 7.9 |
| 19 | 261 | 24 | 38 | 92 | 107 | 24 | 38 | 0 | 3 | 6.9 |
| 20 | 68 | 3 | 11 | 28 | 26 | 3 | 11 | 0 | 2 | 1.5 |
| 21 | 67 | 8 | 7 | 25 | 27 | 8 | 7 | 0 | 0 | 1.9 |
| 22 | 322 | 28 | 53 | 118 | 123 | 28 | 53 | 0 | 2 | 9.2 |
| 23 | 241 | 27 | 40 | 87 | 87 | 27 | 39 | 1 | 5 | 7.2 |
| 24 | 237 | 26 | 40 | 81 | 90 | 26 | 40 | 0 | 3 | 7.2 |
| 25 | 275 | 30 | 40 | 94 | 111 | 30 | 40 | 0 | 1 | 7.8 |
| 26 | 254 | 38 | 33 | 81 | 102 | 38 | 33 | 0 | 3 | 8.2 |
| 27 | 63 | 5 | 7 | 29 | 22 | 5 | 7 | 0 | 0 | 1.5 |
| 28 | 65 | 3 | 15 | 23 | 24 | 3 | 15 | 0 | 0 | 2.0 |

## Categories at the end

| Category | Policy | Effective | Evidence (items) | Precision |
|---|---|---|---|---|
| password_reset | A3 | A3 | 285 | 96.8% |
| account_unlock | A3 | A3 | 127 | 94.5% |
| how_to_question | A3 | A3 | 105 | 98.1% |
| vpn_access | A1 | A1 | 0 | n/a |
| email_issue | A1 | A1 | 0 | n/a |
| software_install | A1 | A1 | 0 | n/a |
| printer_issue | A1 | A1 | 0 | n/a |
| laptop_hardware | A1 | A1 | 0 | n/a |
| app_access_request | A1 | A1 | 0 | n/a |
| app_error | A1 | A1 | 0 | n/a |
| payroll_access | A1 | A1 | 0 | n/a |
| payroll_discrepancy | A1 | A1 | 0 | n/a |
| expense_claim_issue | A1 | A1 | 0 | n/a |
| security_phishing | A0 | A0 | 0 | n/a |
| network_outage | A0 | A0 | 0 | n/a |
