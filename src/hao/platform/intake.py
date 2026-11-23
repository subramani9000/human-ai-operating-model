"""Intake pipeline: redact → classify → record → tell the humans (work note). No actions yet (Phase 3+)."""

from __future__ import annotations

from collections import Counter

from sqlalchemy.orm import Session, sessionmaker

from hao.adapters.base import TicketingAdapter, TicketingError
from hao.db.models import HaoIntakeResult, HaoProcessedEvent
from hao.intake.classifiers import Classifier
from hao.intake.redaction import Redactor


def work_note(result: HaoIntakeResult) -> str:
    return (f"[AI triage · {result.classifier}] category: {result.category} · application: "
            f"{result.application or 'n/a'} · confidence {result.confidence:.2f}. "
            "Suggestion only, no action taken (autonomy routing arrives in Phase 3).")


def process_created(
    incident: dict,
    *,
    delivery_id: str,
    session_factory: sessionmaker[Session],
    adapter: TicketingAdapter,
    classifier: Classifier,
    redactor: Redactor,
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
    )
    try:
        adapter.add_work_note(sys_id, work_note(result))
        result.work_note_status = "written"
    except TicketingError as exc:
        result.work_note_status, result.work_note_error = "failed", str(exc)[:500]

    with session_factory() as session:
        session.add(result)
        _mark(session, delivery_id, "processed" if result.work_note_status == "written" else "failed",
              result.work_note_error)
        session.commit()
        session.refresh(result)
    return result


def _mark(session: Session, delivery_id: str, status: str, detail: str = "") -> None:
    ev = session.query(HaoProcessedEvent).filter_by(delivery_id=delivery_id).first()
    if ev:
        ev.status, ev.detail = status, detail or ""
    session.commit()
