"""Data model.

``client_*`` tables simulate the client's ticketing system. Platform code never writes to them
directly; it goes through the ticketing REST API (ADR 0002).
``hao_*`` tables belong to the Human+AI operating model platform.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------- client system (simulated)


class ClientIncident(Base):
    __tablename__ = "client_incidents"

    sys_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    number: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sys_updated_on: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    caller_id: Mapped[str] = mapped_column(String(120), default="")
    caller_email: Mapped[str] = mapped_column(String(200), default="")
    u_vip: Mapped[bool] = mapped_column(Boolean, default=False)
    location: Mapped[str] = mapped_column(String(80), default="")
    contact_type: Mapped[str] = mapped_column(String(40), default="self-service")
    short_description: Mapped[str] = mapped_column(String(300), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(80), default="")
    cmdb_ci: Mapped[str] = mapped_column(String(80), default="")
    priority: Mapped[str] = mapped_column(String(2), default="4")
    state: Mapped[str] = mapped_column(String(2), default="1", index=True)
    assignment_group: Mapped[str] = mapped_column(String(80), default="SD-L1", index=True)
    assigned_to: Mapped[str] = mapped_column(String(120), default="")
    close_code: Mapped[str] = mapped_column(String(80), default="")
    close_notes: Mapped[str] = mapped_column(Text, default="")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reopen_count: Mapped[int] = mapped_column(Integer, default=0)

    work_notes: Mapped[list[ClientWorkNote]] = relationship(back_populates="incident", cascade="all, delete")


class ClientWorkNote(Base):
    __tablename__ = "client_work_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_sys_id: Mapped[str] = mapped_column(ForeignKey("client_incidents.sys_id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="work_note")  # work_note | comment
    author: Mapped[str] = mapped_column(String(120))
    note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    incident: Mapped[ClientIncident] = relationship(back_populates="work_notes")


class ClientWebhookSubscription(Base):
    __tablename__ = "client_webhook_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(500))
    events: Mapped[str] = mapped_column(String(200), default="incident.created,incident.updated")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ClientWebhookDelivery(Base):
    __tablename__ = "client_webhook_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    delivery_id: Mapped[str] = mapped_column(String(40), index=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("client_webhook_subscriptions.id"))
    event: Mapped[str] = mapped_column(String(40))
    incident_sys_id: Mapped[str] = mapped_column(String(32))
    attempt: Mapped[int] = mapped_column(Integer)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str] = mapped_column(Text, default="")
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- Human+AI operating model platform


class HaoProcessedEvent(Base):
    """Idempotency ledger for inbound webhooks: they are at-least-once, we must be exactly-once."""

    __tablename__ = "hao_processed_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    delivery_id: Mapped[str] = mapped_column(String(64), unique=True)
    event: Mapped[str] = mapped_column(String(40))
    incident_sys_id: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(20), default="accepted")  # accepted | processed | ignored | failed
    detail: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HaoIntakeResult(Base):
    """Output of intake (redact + classify). Stores redacted text only: no raw PII on the platform side."""

    __tablename__ = "hao_intake_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_sys_id: Mapped[str] = mapped_column(String(32), unique=True)
    number: Mapped[str] = mapped_column(String(16), default="")
    redacted_short_description: Mapped[str] = mapped_column(String(400), default="")
    redacted_description: Mapped[str] = mapped_column(Text, default="")
    pii_entities: Mapped[dict] = mapped_column(JSON, default=dict)  # counts per type, never values
    category: Mapped[str] = mapped_column(String(80))
    application: Mapped[str] = mapped_column(String(80), default="")
    confidence: Mapped[float] = mapped_column(Float)
    signals: Mapped[str] = mapped_column(String(300), default="")
    classifier: Mapped[str] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(60), default="")
    prompt_version: Mapped[str] = mapped_column(String(20), default="")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    classifier_error: Mapped[str] = mapped_column(Text, default="")
    work_note_status: Mapped[str] = mapped_column(String(20), default="pending")  # written | failed
    work_note_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HaoDecision(Base):
    """One routing decision per ticket event: which autonomy level, and why."""

    __tablename__ = "hao_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_sys_id: Mapped[str] = mapped_column(String(32), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    predicted_category: Mapped[str] = mapped_column(String(80))
    predicted_application: Mapped[str] = mapped_column(String(80), default="")
    risk_tier: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[float] = mapped_column(Float)
    level: Mapped[str] = mapped_column(String(2))  # A0..A3
    owner: Mapped[str] = mapped_column(String(60), default="")  # accountable owner key (ownership.yaml)
    assignment_group: Mapped[str] = mapped_column(String(80), default="")
    explanation: Mapped[str] = mapped_column(Text, default="")
    steps: Mapped[list] = mapped_column(JSON, default=list)
    policy_version: Mapped[str] = mapped_column(String(16))
    applied_rules: Mapped[list] = mapped_column(JSON, default=list)
    model: Mapped[str] = mapped_column(String(60), default="")
    prompt_version: Mapped[str] = mapped_column(String(20), default="")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost_inr: Mapped[float] = mapped_column(Float, default=0.0)


class HaoAuditEvent(Base):
    """Append-only, hash-chained audit log. See hao.audit.log and ADR 0007."""

    __tablename__ = "hao_audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decision_id: Mapped[int | None] = mapped_column(ForeignKey("hao_decisions.id"), nullable=True)
    incident_sys_id: Mapped[str] = mapped_column(String(32), index=True)
    actor: Mapped[str] = mapped_column(String(120))  # system | ai:<model> | user:<email>
    action: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    policy_version: Mapped[str] = mapped_column(String(16), default="")
    prev_hash: Mapped[str] = mapped_column(String(64), unique=True)  # unique: the chain cannot fork
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class HaoFeedback(Base):
    """Every human verdict on an AI output. Overrides are signal, not noise."""

    __tablename__ = "hao_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_id: Mapped[int] = mapped_column(ForeignKey("hao_decisions.id"), index=True)
    reviewer: Mapped[str] = mapped_column(String(200))
    verdict: Mapped[str] = mapped_column(String(20))  # approved | edited | rejected | rerouted
    reason_code: Mapped[str] = mapped_column(String(60), default="")
    corrected_category: Mapped[str] = mapped_column(String(80), default="")
    corrected_group: Mapped[str] = mapped_column(String(80), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HaoAutonomyChange(Base):
    """History of autonomy level changes per category: promotions, demotions, who approved."""

    __tablename__ = "hao_autonomy_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(80), index=True)
    from_level: Mapped[str] = mapped_column(String(2))
    to_level: Mapped[str] = mapped_column(String(2))
    direction: Mapped[str] = mapped_column(String(10))  # promote | demote
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)  # precision, samples, window
    status: Mapped[str] = mapped_column(String(20), default="proposed")  # proposed | approved | applied
    approved_by: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
