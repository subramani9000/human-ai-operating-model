# 02 — Stakeholder map

## Power / interest grid

|  | **Low interest** | **High interest** |
|---|---|---|
| **High power** | CFO (cares only about cost per ticket) — *keep satisfied* | Ramesh Iyer (sponsor), Sanjay Mehta (audit), Farah Khan (CISO) — *manage closely* |
| **Low power** | Wider employee base (end users) — *monitor* | Priya Nair (L1 lead), L1/L2 agents, Lakshmi Sundaram (HR) and the employee representative, Arjun Rao (app owner) — *keep informed, co-design* |

## What each stakeholder needs from the system

| Stakeholder | Wins if… | Blocks us if… | Artefact they care about |
|---|---|---|---|
| Ramesh (sponsor) | cost per ticket and P3 resolution time improve visibly | there's no number for the leadership review | Leadership dashboard |
| Sanjay (audit) | every AI action has a policy reference and a tamper-evident log | "the model decided" | Audit log + policy versions |
| Farah (CISO) | no raw PII leaves the boundary; redaction is tested | redaction is untested | Redaction eval report |
| Arjun (app owner) | finance/HR systems are never touched autonomously | any A3 action on FinCore/PeopleDesk | Policy hard caps |
| Priya (L1 lead) | fewer bounced tickets; agents aren't blamed for AI errors | agents feel monitored or replaced | Review console, ownership registry |
| L2 (Vikram) | escalations arrive with useful context | summaries hallucinate | Handoff packet |
| HR + employee representative | human time is redirected, not cut, and that's visible | automation rate is the only metric | "Hours redirected" metric |

## RACI for the operating model

| Activity | Responsible | Accountable | Consulted | Informed |
|---|---|---|---|---|
| Define risk tier per category | FDE team | Ramesh | Audit, CISO, app owners | L1/L2 |
| Approve autonomy promotion | Category owner | Ramesh | FDE team (eval evidence) | Audit |
| Automatic demotion on quality drop | System | Category owner | — | Ramesh, Audit |
| Review A2 drafts | L1/L2 agents | Priya / Vikram | — | Category owner |
| Policy change (YAML PR) | FDE team | Ramesh | Audit | All owners |
| Incident from an AI action | Category owner | Ramesh | FDE team, CISO | Audit |
