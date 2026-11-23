# 00 — Problem statement

> **Client:** Kaveri Logistics (fictional) · **Engagement:** Human+AI operating model pilot · **Duration:** 8 weeks

## Client context

Kaveri Logistics is a 6,000-person logistics company operating across India and Southeast Asia. Its central operations function runs the IT service desk and operations support for 40+ internal applications — roughly **18,000 tickets a month**, handled by **65 people across L1, L2 and L3**.

Over the past year, three separate teams built three separate AI pilots:

| Pilot | Built by | What happened |
|---|---|---|
| Employee chatbot | Digital workplace team | "Resolves" 12% of tickets — but 30% of those reopen |
| Ticket auto-categoriser | IT ops tooling team | 78% accurate; agents manually re-route ~22% |
| LLM summariser for L2 | An L2 engineer, as a side project | Output ignored — nobody trusts it |

Each works in isolation. Together they've made things worse.

## What's broken

1. **No decision rights.** Nobody has defined what AI may do on its own. The chatbot closes tickets unreviewed; the summariser is ignored.
2. **Lossy handoffs.** When AI escalates, the human receives a bare ticket and starts from zero. AI added latency instead of removing it.
3. **No accountability.** When the chatbot wrongly closed a payroll-access ticket, nobody could say who owned the mistake.
4. **No audit trail.** Internal audit asked for a log of every AI action touching finance-related systems. It could not be produced.
5. **No learning loop.** Human corrections (re-routes, edits, rejections) are thrown away.
6. **Trust and people.** L1 agents see AI as a threat and route around it. Leadership sees no ROI and is close to scrapping all three pilots.

## Core problem

> Kaveri has AI **capabilities** but no **operating model**: no defined way of deciding, per type of work, **who does it (AI or human), at what level of autonomy, under what oversight, with what accountability, and how the system learns from human judgement.** Without that, AI creates risk and rework instead of capacity.

## What the client wants

A working operating model — not a slide deck — that:

- **Classifies work by risk** and assigns an autonomy level per task type: *AI acts* · *AI drafts, human approves* · *human leads, AI assists* · *human only*.
- **Hands off with full context** in both directions.
- **Adjusts autonomy from evidence** — task types earn autonomy as accuracy is proven, and lose it automatically when quality drops.
- **Makes every AI decision traceable**: input, action, confidence, the policy that allowed it, who approved it.
- **Captures every human override** as feedback.
- **Assigns a named human owner** to every task category and every AI action.
- **Gives leadership one view** of throughput, quality, cost, risk and team load.

## Constraints

- **Integrate, don't replace.** The existing ServiceNow-style ticketing tool stays.
- **Data boundaries.** Employee and payroll data must be redacted before reaching any LLM.
- **High-risk domains.** Finance and HR systems may never be acted on by AI without human approval.
- **People.** HR and the employee representative must be able to see that the model moves people to higher-value work, not silently removes roles.
- **Scope.** One workflow (IT service desk) in 8 weeks; the design must be reusable for HR ops and finance ops.

## Success metrics (pilot)

| Metric | Baseline | Target |
|---|---|---|
| Tickets handled at AI-acts level (low-risk categories) | ~12%, unreviewed | 35–40% at ≥ 95% precision |
| Reopen rate on AI-resolved tickets | 30% | < 5% |
| Misrouting after AI categorisation | 22% | < 8% |
| Median time to resolve (P3) | ~9 h | −30% |
| AI actions with complete audit record | 0% | 100% |
| Human overrides captured as feedback | 0% | 100% |
| Agent trust score (survey) | baseline | +20 pts |

## Out of scope

Replacing the ticketing system · custom model training or fine-tuning · autonomous actions on finance or HR systems.
