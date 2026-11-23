"""Tamper-evident audit log (ADR 0007).

Every event stores ``hash = sha256(prev_hash ‖ canonical(event))``. Editing, deleting or reordering any
past row breaks every hash after it, and ``verify()`` says exactly where. ``prev_hash`` is unique, so
two writers can't both extend the same head (no forks). The loser retries on the new head.

Tamper-*evident*, not tamper-*proof*: someone with DB access could rewrite the whole chain. That's
why the head hash is **anchored** outside the database (``hao audit head`` → audit's mailbox / WORM
storage). A rewritten chain no longer matches the last anchored head.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from hao.db.models import HaoAuditEvent

GENESIS = "0" * 64
_WRITE_LOCK = threading.Lock()


def _ts(value: datetime) -> str:
    if value.tzinfo is None:  # SQLite returns naive datetimes; we always store UTC
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def compute_hash(prev_hash: str, *, ts: datetime, incident_sys_id: str, actor: str, action: str, payload: dict,
                 policy_version: str, decision_id: int | None) -> str:
    canonical = json.dumps(
        {"ts": _ts(ts), "incident": incident_sys_id, "actor": actor, "action": action, "payload": payload,
         "policy_version": policy_version, "decision_id": decision_id},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str,
    )
    return hashlib.sha256(f"{prev_hash}|{canonical}".encode()).hexdigest()


def _hash_of(ev: HaoAuditEvent) -> str:
    return compute_hash(ev.prev_hash, ts=ev.ts, incident_sys_id=ev.incident_sys_id, actor=ev.actor,
                        action=ev.action, payload=ev.payload, policy_version=ev.policy_version,
                        decision_id=ev.decision_id)


def head(session: Session) -> HaoAuditEvent | None:
    return session.scalar(select(HaoAuditEvent).order_by(HaoAuditEvent.id.desc()).limit(1))


def append(session: Session, *, incident_sys_id: str, actor: str, action: str, payload: dict | None = None,
           policy_version: str = "", decision_id: int | None = None, max_retries: int = 5) -> HaoAuditEvent:
    """Append one event and commit. Payloads must never contain raw PII (redacted text and counts only)."""
    payload = json.loads(json.dumps(payload or {}, default=str))  # freeze to JSON-native types
    for _ in range(max_retries):
        with _WRITE_LOCK:
            last = head(session)
            prev = last.hash if last else GENESIS
            ts = datetime.now(UTC)
            ev = HaoAuditEvent(ts=ts, incident_sys_id=incident_sys_id, actor=actor, action=action, payload=payload,
                               policy_version=policy_version, decision_id=decision_id, prev_hash=prev)
            ev.hash = compute_hash(prev, ts=ts, incident_sys_id=incident_sys_id, actor=actor, action=action,
                                   payload=payload, policy_version=policy_version, decision_id=decision_id)
            session.add(ev)
            try:
                session.commit()
                return ev
            except IntegrityError:  # another writer extended the head first: retry on the new head
                session.rollback()
    raise RuntimeError("could not append audit event after retries")


@dataclass
class VerifyResult:
    ok: bool
    events: int
    head_hash: str
    broken_at_id: int | None = None
    reason: str = ""


def verify(session_factory: sessionmaker[Session], expected_head: str | None = None) -> VerifyResult:
    """Walk the chain from genesis. Optionally check it ends at an externally anchored head hash."""
    prev, count, last_hash = GENESIS, 0, GENESIS
    with session_factory() as session:
        for ev in session.scalars(select(HaoAuditEvent).order_by(HaoAuditEvent.id)):
            if ev.prev_hash != prev:
                return VerifyResult(False, count, last_hash, ev.id, "prev_hash does not match previous event "
                                                                    "(row deleted, inserted or reordered)")
            if _hash_of(ev) != ev.hash:
                return VerifyResult(False, count, last_hash, ev.id, "content does not match its hash (row edited)")
            prev = last_hash = ev.hash
            count += 1
    if expected_head and expected_head != last_hash and not _contains(session_factory, expected_head):
        return VerifyResult(False, count, last_hash, None, "anchored head hash not found in chain (chain rewritten)")
    return VerifyResult(True, count, last_hash)


def _contains(session_factory: sessionmaker[Session], h: str) -> bool:
    with session_factory() as session:
        return session.scalar(select(HaoAuditEvent.id).where(HaoAuditEvent.hash == h)) is not None


def trail(session: Session, incident_sys_id: str) -> list[dict]:
    rows = session.scalars(select(HaoAuditEvent).where(HaoAuditEvent.incident_sys_id == incident_sys_id)
                           .order_by(HaoAuditEvent.id))
    return [{"id": e.id, "ts": _ts(e.ts), "actor": e.actor, "action": e.action, "payload": e.payload,
             "policy_version": e.policy_version, "decision_id": e.decision_id, "hash": e.hash} for e in rows]
