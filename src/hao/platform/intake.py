"""Intake pipeline: redact → classify → decide autonomy → record + audit → tell the humans (work note).

Phase 3 decides the autonomy level but still takes **no action** on the ticket; agent lanes arrive in Phase 4.
"""

from __future__ import annotations

from collections import Counter

from sqlalchemy.orm import Session, sessionmaker

from hao.adapters.base import TicketingAdapter, TicketingError
from hao.audit import log as audit
from hao.db.models import HaoDecision, HaoIntakeResult, HaoProcessedEvent
from hao.intake.classifiers import Classifier
from hao.intake.redaction import Redactor
from hao.policy.engine import Decision, DecisionInput, decide
from hao.policy.schema import Policy


def work_note(result: HaoIntakeResult, decision: Decision) -> str:
    return (
        f"[AI triage · {result.classifier}] category: {result.category} · application: "
        f"{result.application or 'n/a'} · confidence {result.confidence:.2f}\n"
        f"Autonomy: {decision.explain()}\n"
        f"Accountable owner: {decision.owner_name} · suggested group: {decision.assignment_group} · "
        f"policy {decision.policy_label} ({decision.policy_version})\n"
        "No action taken yet: agent lanes arrive in Phase 4."
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
    d = decide(policy, decision_input(incident, result))

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
        _mark(session, delivery_id, "processed" if status == "written" else "failed", error)
        session.refresh(stored)
        return stored


def _mark(session: Session, delivery_id: str, status: str, detail: str = "") -> None:
    ev = session.query(HaoProcessedEvent).filter_by(delivery_id=delivery_id).first()
    if ev:
        ev.status, ev.detail = status, detail or ""
    session.commit()
