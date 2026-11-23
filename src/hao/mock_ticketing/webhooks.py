"""Outbound webhooks from the simulated client system.

Behaves like real enterprise ticketing webhooks: HMAC-signed, retried with backoff,
**at-least-once** (may duplicate), and only as reliable as the receiver.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import random
import time
import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from hao.db.models import ClientWebhookDelivery, ClientWebhookSubscription

SIGNATURE_HEADER = "X-Kaveri-Signature"
EVENT_HEADER = "X-Kaveri-Event"
DELIVERY_HEADER = "X-Kaveri-Delivery"


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify(body: bytes, signature: str, secret: str) -> bool:
    """Receivers (our platform) call this before trusting a payload."""
    return hmac.compare_digest(sign(body, secret), signature or "")


def deliver_event(
    session_factory: sessionmaker[Session],
    event: str,
    incident: dict,
    *,
    secret: str,
    max_attempts: int,
    duplicate_rate: float = 0.0,
    transport: httpx.BaseTransport | None = None,
    backoff_s: float = 0.5,
    rng: random.Random | None = None,
) -> None:
    rng = rng or random.Random()
    with session_factory() as session:
        subs = session.scalars(select(ClientWebhookSubscription).where(ClientWebhookSubscription.active)).all()
        targets = [(s.id, s.url) for s in subs if event in s.events.split(",")]

    payload = {
        "event": event,
        "sent_at": datetime.now(UTC).isoformat(),
        "incident": incident,
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    headers = {
        "Content-Type": "application/json",
        SIGNATURE_HEADER: sign(body, secret),
        EVENT_HEADER: event,
    }

    with httpx.Client(transport=transport, timeout=5.0) as client:
        for sub_id, url in targets:
            delivery_id = uuid.uuid4().hex
            sends = 2 if rng.random() < duplicate_rate else 1  # at-least-once semantics
            for _ in range(sends):
                _send_with_retry(client, session_factory, sub_id, url, event, incident["sys_id"], body,
                                 {**headers, DELIVERY_HEADER: delivery_id}, max_attempts, backoff_s)


def _send_with_retry(client, session_factory, sub_id, url, event, sys_id, body, headers, max_attempts, backoff_s):
    for attempt in range(1, max_attempts + 1):
        status, error, ok = None, "", False
        try:
            resp = client.post(url, content=body, headers=headers)
            status, ok = resp.status_code, 200 <= resp.status_code < 300
            if not ok:
                error = resp.text[:500]
        except httpx.HTTPError as exc:
            error = f"{type(exc).__name__}: {exc}"[:500]
        with session_factory() as session:
            session.add(ClientWebhookDelivery(
                delivery_id=headers[DELIVERY_HEADER], subscription_id=sub_id, event=event,
                incident_sys_id=sys_id, attempt=attempt, status_code=status, success=ok, error=error,
            ))
            session.commit()
        if ok:
            return
        if attempt < max_attempts and backoff_s:
            time.sleep(backoff_s * 2 ** (attempt - 1))
