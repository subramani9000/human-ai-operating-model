"""Intake pipeline: redact → classify → decide autonomy → record + audit → tell the humans (work note).

The lane for the chosen level (hao.agents.lanes) then routes, drafts, waits for approval, or acts.
"""

from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from hao import clock
from hao.adapters.base import TicketingAdapter, TicketingError
from hao.agents.lanes import LaneDeps, run_lane
from hao.audit import log as audit
from hao.controller.autonomy import load_caps
from hao.db.models import HaoDecision, HaoFeedback, HaoIntakeResult, HaoProcessedEvent
from hao.intake.classifiers import Classifier
from hao.intake.redaction import Redactor
from hao.policy.engine import Decision, DecisionInput, decide
from hao.policy.schema import Policy

LANE_HINT = {
    "A0": "Human only: no AI action will be taken.",
    "A1": "AI will route the ticket and post a handoff packet; a human leads.",
    "A2": "AI will draft actions and a reply for human approval.",
    "A3": "AI will act within its playbook; a sample is QA-reviewed.",
}


def work_note(result: HaoIntakeResult, decision: Decision) -> str:
    return (
        f"[AI triage · {result.classifier}] category: {result.category} · application: "
        f"{result.application or 'n/a'} · confidence {result.confidence:.2f}\n"
        f"Autonomy: {decision.explain()}\n"
        f"Accountable owner: {decision.owner_name} · suggested group: {decision.assignment_group} · "
        f"policy {decision.policy_label} ({decision.policy_version})\n"
        + LANE_HINT[decision.level.value]
    )


def decision_input(incident: dict, result: HaoIntakeResult) -> DecisionInput:
    return DecisionInput(
        category=result.category,
        application=result.application,
        confidence=result.confidence,
        priority=str(incident.get("priority") or "4"),
        requester_is_vip=str(incident.get("u_vip", "false")).lower() == "true",
        reopen_count=int(incident.get("reopen_count") or 0),
        text=f"{result.redacted_short_description}\n{result.redacted_description}",
    )


def process_created(
    incident: dict,
    *,
    delivery_id: str,
    session_factory: sessionmaker[Session],
    adapter: TicketingAdapter,
    classifier: Classifier,
    redactor: Redactor,
    policy: Policy,
    actor: str = "ai:hao-intake",
    lane_deps: LaneDeps | None = None,
) -> HaoIntakeResult | None:
    sys_id = incident["sys_id"]
    with session_factory() as session:
        if session.query(HaoIntakeResult).filter_by(incident_sys_id=sys_id).first():
            _mark(session, delivery_id, "ignored", "already triaged")  # duplicate created event
            return None

    redacted, entities, _vault = redactor.redact_ticket(incident)  # vault is not persisted (ADR 0004)
    pred = classifier.classify(redacted)
    result = HaoIntakeResult(
        incident_sys_id=sys_id,
        number=incident.get("number", ""),
        redacted_short_description=redacted.get("short_description", "")[:400],
        redacted_description=redacted.get("description", ""),
        pii_entities=dict(Counter(e.type for e in entities)),
        category=pred.category,
        application=pred.application,
        confidence=pred.confidence,
        signals=pred.signals[:300],
        classifier=pred.classifier,
        model=pred.model,
        prompt_version=pred.prompt_version,
        latency_ms=pred.latency_ms,
        input_tokens=pred.input_tokens,
        output_tokens=pred.output_tokens,
        cost_usd=pred.cost_usd,
        classifier_error=pred.error,
        work_note_status="pending",
        work_note_error="",
    )
    x = decision_input(incident, result)
    with session_factory() as session:
        caps = load_caps(session)  # runtime demotions from the autonomy controller
    d = decide(policy, x, caps)

    with session_factory() as session:
        session.add(result)
        row = HaoDecision(
            incident_sys_id=sys_id, predicted_category=result.category, predicted_application=result.application,
            risk_tier=d.risk_tier, confidence=result.confidence, level=d.level.value, owner=d.owner,
            assignment_group=d.assignment_group, explanation=d.explain(), steps=d.steps,
            policy_version=d.policy_version, applied_rules=d.applied_rules, model=result.model or result.classifier,
            prompt_version=result.prompt_version, latency_ms=result.latency_ms, cost_inr=0.0,
        )
        session.add(row)
        session.commit()
        decision_id = row.id

        common = {"incident_sys_id": sys_id, "policy_version": d.policy_version, "decision_id": decision_id}
        audit.append(session, actor=actor, action="intake.redacted",
                     payload={"pii_entities": result.pii_entities}, **common)
        audit.append(session, actor=f"ai:{result.classifier}", action="intake.classified",
                     payload={"category": result.category, "application": result.application,
                              "confidence": result.confidence, "model": result.model,
                              "prompt_version": result.prompt_version, "error": result.classifier_error}, **common)
        audit.append(session, actor="system:policy-engine", action="autonomy.decided",
                     payload={"level": d.level.value, "explanation": d.explain(), "steps": d.steps,
                              "owner": d.owner, "assignment_group": d.assignment_group,
                              "policy_label": d.policy_label}, **common)

    try:
        adapter.add_work_note(sys_id, work_note(result, d))
        status, error = "written", ""
    except TicketingError as exc:
        status, error = "failed", str(exc)[:500]

    with session_factory() as session:
        stored = session.query(HaoIntakeResult).filter_by(incident_sys_id=sys_id).one()
        stored.work_note_status, stored.work_note_error = status, error
        session.commit()
        action = "ticket.annotated" if status == "written" else "ticket.annotate_failed"
        audit.append(session, actor=actor, action=action, payload={"error": error}, incident_sys_id=sys_id,
                     policy_version=d.policy_version, decision_id=decision_id)
        session.refresh(stored)

    if lane_deps is not None:  # Phase 4: act on the decision
        run_lane(incident, stored, d, decision_id, x, lane_deps)

    with session_factory() as session:
        _mark(session, delivery_id, "processed" if status == "written" else "failed", error)
    return stored


def _mark(session: Session, delivery_id: str, status: str, detail: str = "") -> None:
    ev = session.query(HaoProcessedEvent).filter_by(delivery_id=delivery_id).first()
    if ev:
        ev.status, ev.detail = status, detail or ""
    session.commit()


def process_reopen(incident: dict, *, delivery_id: str, session_factory: sessionmaker[Session], policy: Policy,
                   now=None) -> HaoFeedback | None:
    """A user reopened a ticket. If the AI resolved it (A3, or an approved A2) within the reopen window,
    that is negative evidence for the category, and may trigger an immediate demotion."""
    from datetime import UTC, timedelta

    from hao.controller.autonomy import check_demotion
    from hao.db.models import HaoWorkItem

    sys_id, count = incident["sys_id"], int(incident.get("reopen_count") or 0)
    now = now or clock.now()
    with session_factory() as session:
        item = session.scalars(select(HaoWorkItem).where(HaoWorkItem.incident_sys_id == sys_id,
                                                         HaoWorkItem.state == "executed")
                               .order_by(HaoWorkItem.id.desc()).limit(1)).first()
        marker = f"reopen #{count}"
        if item is None:
            _mark(session, delivery_id, "ignored", "reopened ticket was not resolved by AI")
            return None
        done_at = item.updated_at if item.updated_at.tzinfo else item.updated_at.replace(tzinfo=UTC)
        if now - done_at > timedelta(hours=policy.controller.reopen_window_hours):
            _mark(session, delivery_id, "ignored", "reopened outside the reopen window")
            return None
        if session.scalar(select(HaoFeedback).where(HaoFeedback.work_item_id == item.id,
                                                    HaoFeedback.verdict == "reopened",
                                                    HaoFeedback.note == marker)):
            _mark(session, delivery_id, "ignored", "reopen already recorded")
            return None
        fb = HaoFeedback(decision_id=item.decision_id, work_item_id=item.id, category=item.category,
                         level=item.level, reviewer="system:ticketing", verdict="reopened",
                         reason_code="reopened_by_user", corrected_category="", corrected_group="", note=marker)
        session.add(fb)
        session.commit()
        audit.append(session, incident_sys_id=sys_id, actor="system:ticketing", action="feedback.reopened",
                     payload={"work_item": item.id, "category": item.category, "level": item.level,
                              "reopen_count": count}, policy_version=policy.version, decision_id=item.decision_id)
        check_demotion(session, policy, item.category)
        _mark(session, delivery_id, "processed", marker)
        session.refresh(fb)
        return fb
