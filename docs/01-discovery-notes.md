# 01 — Discovery notes

> Week 1 of the engagement. All people, quotes and numbers are fictional, written to simulate a realistic enterprise discovery.

## Sessions held

| # | Session | Stakeholder(s) | Format |
|---|---|---|---|
| D1 | Kick-off and goals | Ramesh Iyer — Head of IT Operations (sponsor) | 60 min |
| D2 | Service desk shadowing | Priya Nair — L1 lead, plus 4 L1 agents | Half day, on the floor |
| D3 | L2/L3 escalation walkthrough | Vikram Shetty — L2 manager, apps support | 45 min |
| D4 | Audit and risk | Sanjay Mehta — Internal audit | 45 min |
| D5 | Security and data handling | Farah Khan — CISO office | 45 min |
| D6 | Finance and HR systems | Arjun Rao — FinCore and PeopleDesk app owner | 30 min |
| D7 | People impact | Lakshmi Sundaram — HR business partner, with the employee representative | 45 min |
| D8 | Existing AI pilots | Owners of the chatbot, categoriser and summariser | 90 min |

## What we heard

### D1 — Sponsor (Ramesh)
- "I don't need more AI. I need the AI we have to be **safe enough that I can defend it** to the CFO and audit."
- His definition of success is a number he can take to the leadership review: *cost per ticket* and *P3 resolution time*.
- He is open to scrapping pilots, but would rather "fix the plumbing."

### D2 — Shadowing the floor (Priya + agents)
- Agents keep the chatbot transcript open in one tab and the ticket in another. **There is no link between them.** Agents re-ask users the same questions.
- About 1 in 5 tickets arrive in the wrong queue. Agents bounce them by hand and add a work note like *"wrong grp, pls check"*.
- Ticket text is **messy**: Tanglish/Hinglish, forwarded email chains, signatures with phone numbers, screenshots described as *"see attached"* with no attachment.
- A large share of tickets have **no category set by the user**, or the wrong one ("Other" is the most common user-selected category).
- Quote (L1 agent): *"When the bot closes something wrong, the user calls me angry and I look bad, not the bot."*

### D3 — Escalations (Vikram)
- The L2 summary is ignored because it "hallucinated a root cause once, and that was enough."
- What L2 actually wants on escalation: **what was already tried, which app and environment, the error text, and whether this looks like an existing known issue.**

### D4 — Audit (Sanjay)
- Requires that for any automated action: *what was decided, by what logic, on what data, approved by whom, when* — **retrievable for 7 years**.
- Will not accept "the model decided." There must be a **policy reference** behind every action.
- Wants evidence the log can't be silently edited.

### D5 — Security (Farah)
- No raw employee PII (names, phone numbers, employee IDs, personal emails) may be sent to an external LLM API.
- Redaction must be **demonstrable and tested**, not just claimed.
- All LLM calls must be logged with cost and latency.

### D6 — Finance and HR systems (Arjun)
- Payroll and expense systems are **hard red lines**. AI may draft; it may **never execute** an action there.
- "Payroll access" tickets look like normal access requests but carry far more risk. **Category alone is not enough. The target application matters.**

### D7 — People (Lakshmi + employee representative)
- The biggest fear is invisible headcount reduction.
- They asked for the operating model to report **where human time goes** (e.g. "L1 hours moved from password resets to complex cases"), not just "tickets automated."
- Agents must be able to **override the AI without penalty**, and that override must count as valued input.

### D8 — Existing pilots
- The chatbot has no confidence threshold. It answers everything.
- The categoriser's confidence scores exist but are **never used**.
- No pilot has an owner accountable for its outcomes. All three were "experiments."

## Conflicting requirements

| Tension | Between | How we'll handle it |
|---|---|---|
| Speed of automation vs. auditability | Sponsor ↔ Audit | Autonomy is policy-gated; every action carries a policy reference. Speed comes from *safe* categories first. |
| Automation rate vs. job security | Sponsor ↔ HR/representative | Report "human hours redirected" as a first-class metric; humans own promotion of autonomy. |
| Rich context for the LLM vs. no PII | L2 ↔ Security | Redact first; keep a reversible token map **inside** the client boundary for human views only. |
| One policy vs. per-app exceptions | IT ops ↔ App owners | Policy keyed on *category × application × risk tier*, with hard caps per application. |

## Assumptions to validate

1. Ticket volume is roughly 600/day, with peaks on Monday mornings and at month-end (payroll).
2. The ticketing tool can send outbound webhooks on create and update. *Confirmed with IT tooling: yes, but delivery is at-least-once and occasionally delayed.*
3. Historical tickets (12 months) can be exported for evaluation. *Pending: legal approval for use of historical data.*
4. Category owners exist for every category. *Not true today. We'll need to propose a RACI.*

## Open questions

- Who signs off on promoting a category to *AI acts*: the category owner alone, or also the IT ops sponsor?
- What reopen window counts as "AI got it wrong": 72 hours or 7 days?
- Is there budget for LLM spend per ticket? (Initial guidance: under ₹2 per ticket on average.)

## Decisions taken in week 1

- Pilot workflow: **IT service desk**, starting with **password reset, account unlock, VPN, how-to and software install**.
- Finance/HR categories: **capped at "AI drafts, human approves"** permanently for the pilot.
- Since historical data is pending legal approval, we build a **synthetic dataset that mirrors what we saw on the floor** (see `src/hao/synthetic/`), so engineering and evaluation are not blocked.
