"""Leadership dashboard metrics, measured against the pilot targets in docs/00-problem-statement.md.

Every number here can be recomputed from the platform database. Estimates (human hours) are labelled
as estimates and carry their assumptions (policy/value.yaml).
"""

from __future__ import annotations

import statistics
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from hao import clock
from hao.audit import log as audit
from hao.controller import autonomy as ctl
from hao.db.models import (
    HaoAuditEvent,
    HaoAutonomyChange,
    HaoAutonomyOverride,
    HaoDecision,
    HaoFeedback,
    HaoIntakeResult,
    HaoWorkItem,
)
from hao.policy.schema import Policy


def _utc(dt: datetime | None) -> datetime | None:
    return None if dt is None else (dt if dt.tzinfo else dt.replace(tzinfo=UTC))


def load_value(policy_dir: Path) -> dict:
    return yaml.safe_load((policy_dir / "value.yaml").read_text())


def compute(session_factory: sessionmaker[Session], policy: Policy, value: dict, as_of: datetime | None = None,
            days: int = 28) -> dict:
    as_of = as_of or clock.now()
    since = as_of - timedelta(days=days)

    def in_window(dt) -> bool:
        t = _utc(dt)
        return t is not None and since <= t <= as_of

    with session_factory() as s:
        decisions = [d for d in s.scalars(select(HaoDecision)) if in_window(d.received_at)]
        items = [i for i in s.scalars(select(HaoWorkItem)) if in_window(i.created_at)]
        feedback = [f for f in s.scalars(select(HaoFeedback)) if in_window(f.created_at)]
        intake = [r for r in s.scalars(select(HaoIntakeResult)) if in_window(r.created_at)]
        routed = sum(1 for e in s.scalars(select(HaoAuditEvent).where(HaoAuditEvent.action == "action.executed"))
                     if e.payload.get("tool") == "incident.assign" and in_window(e.ts))
        decided_events = {e.decision_id for e in s.scalars(select(HaoAuditEvent)
                                                         .where(HaoAuditEvent.action == "autonomy.decided"))}
        active = s.scalars(select(HaoAutonomyOverride).where(HaoAutonomyOverride.active))
        overrides = [{"category": o.category, "cap": o.cap, "reason": o.reason,
                      "since": _utc(o.created_at).isoformat()} for o in active]
        proposals = [{"id": p.id, "category": p.category, "from": p.from_level, "to": p.to_level,
                      "status": p.status, "approved_by": p.approved_by}
                     for p in s.scalars(select(HaoAutonomyChange).order_by(HaoAutonomyChange.id))]
        categories = ctl.status(s, policy, as_of)
        d_by_id = {d.id: d for d in decisions}
    chain = audit.verify(session_factory)

    n = len(decisions)
    levels = Counter(d.level for d in decisions)
    work = [i for i in items if i.lane != "qa"]
    executed = [i for i in work if i.state == "executed"]
    auto = [i for i in executed if i.lane == "auto"]
    approved = [i for i in executed if i.lane == "draft"]
    verdicts = Counter(f.verdict for f in feedback)
    reviews = verdicts["approved"] + verdicts["edited"] + verdicts["rejected"]
    reopened_items = {f.work_item_id for f in feedback if f.verdict == "reopened"}

    resolve_minutes = []
    for i in executed:
        d = d_by_id.get(i.decision_id)
        if d and _utc(i.updated_at) and _utc(d.received_at):
            resolve_minutes.append((_utc(i.updated_at) - _utc(d.received_at)).total_seconds() / 60)

    hm = value["handle_minutes"]
    saved_auto = sum(hm.get(i.category, 0) for i in auto)
    saved_approved = sum(max(0.0, hm.get(i.category, 0) - value["review_minutes"]) for i in approved)
    qa_cost = (verdicts["qa_pass"] + verdicts["qa_fail"]) * value["qa_minutes"]
    review_cost = reviews * value["review_minutes"]
    rejected_review_cost = verdicts["rejected"] * value["review_minutes"]  # time spent on drafts that didn't help
    hours = (saved_auto + saved_approved - qa_cost - rejected_review_cost) / 60

    fb_items = {f.work_item_id for f in feedback if f.verdict != "reopened"}
    reviewed = [i for i in items if i.reviewer]
    captured = sum(1 for i in reviewed if (i.parent_id if i.lane == "qa" else i.id) in fb_items)
    captured_rate = captured / len(reviewed) if reviewed else None

    costs = [r.cost_usd for r in intake if r.cost_usd is not None]
    pct = lambda a, b: (a / b) if b else None  # noqa: E731

    return {
        "as_of": as_of.isoformat(), "window_days": days, "policy": policy.version_label,
        "policy_version": policy.version,
        "volume": {"tickets": n, "by_level": {lvl: levels.get(lvl, 0) for lvl in ("A3", "A2", "A1", "A0")}},
        "outcomes": {
            "ai_resolved": len(auto), "approved_and_resolved": len(approved),
            "handed_off": sum(1 for i in work if i.state == "handed_off"),
            "awaiting_approval": sum(1 for i in work if i.state == "awaiting_approval"),
            "rejected": sum(1 for i in work if i.state == "rejected"),
            "failed_fallback": sum(1 for i in work if i.state == "failed"),
            "human_only": sum(1 for i in work if i.state == "human_only"),
            "rerouted_by_ai": routed,
        },
        "quality": {
            "reviews": reviews, "approved": verdicts["approved"], "edited": verdicts["edited"],
            "rejected": verdicts["rejected"], "override_rate": pct(verdicts["edited"] + verdicts["rejected"], reviews),
            "qa_pass": verdicts["qa_pass"], "qa_fail": verdicts["qa_fail"],
            "reopened": len(reopened_items),
            "reopen_rate_ai_resolved": pct(len(reopened_items & {i.id for i in executed}), len(executed)),
        },
        "speed": {"median_minutes_to_resolve_ai": statistics.median(resolve_minutes) if resolve_minutes else None,
                  "baseline_p3_hours": value["baseline_p3_resolution_hours"]},
        "value_estimate": {
            "human_hours_redirected": round(hours, 1),
            "minutes_saved_ai_resolved": saved_auto, "minutes_saved_approved_drafts": saved_approved,
            "minutes_spent_reviewing": review_cost, "minutes_spent_on_qa": qa_cost,
            "assumptions": "policy/value.yaml",
        },
        "cost": {"classifier_cost_usd": sum(costs) if costs else None,
                 "tokens": sum(r.input_tokens + r.output_tokens for r in intake)},
        "governance": {
            "audit_chain_ok": chain.ok, "audit_events": chain.events,
            "decisions_with_audit_record": pct(sum(1 for d in decisions if d.id in decided_events), n),
            "human_verdicts_captured": captured_rate,
            "active_demotions": overrides, "proposals": proposals,
        },
        "categories": categories,
        "targets": _targets(n, len(auto), len(executed), reopened_items, executed, decisions, decided_events,
                            captured_rate),
    }


def _targets(n, n_auto, n_exec, reopened_items, executed, decisions, decided_events, captured) -> list[dict]:
    reopen = len(reopened_items & {i.id for i in executed}) / n_exec if n_exec else None
    audited = sum(1 for d in decisions if d.id in decided_events) / n if n else None
    return [
        {"metric": "Tickets resolved by AI acting alone (A3)", "target": "35–40% (low-risk categories)",
         "actual": n_auto / n if n else None, "met": (n_auto / n >= 0.35) if n else None},
        {"metric": "Reopen rate on AI-resolved tickets", "target": "< 5%", "actual": reopen,
         "met": None if reopen is None else reopen < 0.05},
        {"metric": "AI decisions with a complete audit record", "target": "100%", "actual": audited,
         "met": None if audited is None else audited == 1.0},
        {"metric": "Human verdicts captured as feedback", "target": "100%", "actual": captured,
         "met": None if captured is None else captured == 1.0},
    ]
