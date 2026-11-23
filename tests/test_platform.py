"""Adapter + platform, wired end to end against the mock client system (no network)."""

import json
import random

import httpx
import pytest
from fastapi.testclient import TestClient

from hao.adapters.base import FieldNotApplied, TicketingError, TicketingUnavailable
from hao.adapters.servicenow import ServiceNowAdapter
from hao.config import Settings
from hao.intake.classifiers import KeywordClassifier
from hao.mock_ticketing.app import create_app as create_mock
from hao.mock_ticketing.webhooks import sign
from hao.platform.app import create_app as create_platform

KEY = "test-key"
SECRET = "s3cret"


def mock_client(**overrides) -> TestClient:
    s = Settings(database_url="sqlite://", mock_api_key=KEY, webhook_secret=SECRET, **overrides)
    return TestClient(create_mock(s, webhook_backoff_s=0, rng=random.Random(1)))


def adapter_for(client, **kw) -> ServiceNowAdapter:
    return ServiceNowAdapter(client, KEY, actor="ai:test", backoff_s=0, **kw)


def new_incident(client, **fields):
    body = {"short_description": "vpn down", "description": "gateway unreachable", "caller_id": "Anita Manda",
            **fields}
    return client.post("/api/now/table/incident", json=body, headers={"X-API-Key": KEY}).json()["result"]


# ----------------------------------------------------------------------------- adapter

def test_adapter_returns_typed_incident():
    client = mock_client()
    raw = new_incident(client, priority="2", u_vip="true")
    inc = adapter_for(client).get_incident(raw["sys_id"])
    assert inc.priority == 2 and inc.vip is True and inc.opened_at.tzinfo is not None
    assert inc.raw["priority"] == "2"


def test_adapter_retries_through_flaky_client_system():
    client = mock_client(mock_failure_rate=0.5)
    adapter = adapter_for(client, max_attempts=10)
    for _ in range(10):
        adapter.list_incidents(limit=1)
    assert adapter.retries > 0


def test_adapter_gives_up_with_clear_error():
    adapter = adapter_for(mock_client(mock_failure_rate=1.0), max_attempts=3)
    with pytest.raises(TicketingUnavailable, match="after 3 attempts"):
        adapter.list_incidents()


def test_adapter_detects_silently_ignored_fields():
    client = mock_client()
    sys_id = new_incident(client)["sys_id"]
    with pytest.raises(FieldNotApplied, match="assignmentgroup"):
        adapter_for(client).update_incident(sys_id, {"assignmentgroup": "NET-Connectivity"})
    ok = adapter_for(client).update_incident(sys_id, {"assignment_group": "NET-Connectivity"})
    assert ok.assignment_group == "NET-Connectivity"


def test_adapter_surfaces_data_policy_errors():
    client = mock_client()
    sys_id = new_incident(client)["sys_id"]
    with pytest.raises(TicketingError, match="mandatory"):
        adapter_for(client).update_incident(sys_id, {"state": "6"})


# ----------------------------------------------------------------------------- end to end

@pytest.fixture
def wired():
    """mock client system ⇄ platform, connected through in-process transports."""
    holder = {}

    def to_platform(request: httpx.Request) -> httpx.Response:
        resp = holder["platform"].post("/webhooks/ticketing", content=request.content, headers=dict(request.headers))
        return httpx.Response(resp.status_code, content=resp.content)

    def build(**mock_overrides):
        ms = Settings(database_url="sqlite://", mock_api_key=KEY, webhook_secret=SECRET, **mock_overrides)
        mock = TestClient(create_mock(ms, webhook_transport=httpx.MockTransport(to_platform), webhook_backoff_s=0))
        ps = Settings(platform_database_url="sqlite://", mock_api_key=KEY, webhook_secret=SECRET)
        platform = TestClient(create_platform(ps, lambda: adapter_for(mock), KeywordClassifier()))
        holder["platform"] = platform
        mock.post("/api/x_kaveri/webhooks", json={"url": "http://platform/webhooks/ticketing"},
                  headers={"X-API-Key": KEY})
        return mock, platform

    return build


def test_new_ticket_is_triaged_and_annotated(wired):
    mock, platform = wired()
    inc = new_incident(mock, description="VPN keeps dropping.\nAnita Manda\nAnalyst | Kaveri Logistics, Chennai\n"
                                         "Mob: +91 9840012345")
    result = platform.get(f"/intake/{inc['sys_id']}").json()
    assert result["category"] == "vpn_access" and result["work_note_status"] == "written"
    decision = platform.get(f"/decisions/{inc['sys_id']}").json()
    # vpn_access starts at A1; keyword confidence 0.68 < 0.80 triggers the low-confidence downgrade
    assert decision["decision"]["level"] == "A0" and decision["decision"]["owner"] == "network-lead"
    assert decision["decision"]["applied_rules"] == ["low-confidence-downgrade"]
    actions = [e["action"] for e in decision["audit_trail"]]
    assert actions == ["intake.redacted", "intake.classified", "autonomy.decided", "ticket.annotated",
                       "lane.human_only"]
    assert "Anita" not in json.dumps(decision)
    assert platform.get("/audit/verify").json()["ok"] is True
    stored = json.dumps(result)
    assert "Anita" not in stored and "9840012345" not in stored  # no raw PII on the platform side
    assert result["pii_entities"] == {"PERSON": 1, "PHONE": 1}

    notes = mock.get(f"/api/now/table/incident/{inc['sys_id']}/work_notes", headers={"X-API-Key": KEY}).json()
    assert notes["result"][0]["author"] == "ai:test"
    note = notes["result"][0]["note"]
    assert "vpn_access" in note and "Autonomy: A0" in note and "Joseph Mathew" in note and "no AI action" in note


def test_our_own_work_note_does_not_loop(wired):
    mock, platform = wired()
    new_incident(mock)
    m = platform.get("/metrics").json()
    assert m["events"] == {"processed": 1, "ignored": 1}  # created → processed; our note's update → ignored
    assert sum(m["categories"].values()) == 1
    assert sum(m["levels"].values()) == 1


def test_duplicate_webhooks_are_processed_once(wired):
    mock, platform = wired(mock_webhook_duplicate_rate=1.0)
    inc = new_incident(mock)
    notes = mock.get(f"/api/now/table/incident/{inc['sys_id']}/work_notes", headers={"X-API-Key": KEY}).json()
    assert len(notes["result"]) == 1
    assert sum(platform.get("/metrics").json()["categories"].values()) == 1


def test_rejects_unsigned_webhooks(wired):
    _, platform = wired()
    body = json.dumps({"event": "incident.created", "incident": {"sys_id": "x" * 32}}).encode()
    bad = platform.post("/webhooks/ticketing", content=body,
                        headers={"X-Kaveri-Signature": sign(body, "wrong"), "X-Kaveri-Delivery": "d1"})
    assert bad.status_code == 401
    updated = json.dumps({"event": "incident.updated", "incident": {"sys_id": "x" * 32}}).encode()
    good = platform.post("/webhooks/ticketing", content=updated,
                         headers={"X-Kaveri-Signature": sign(updated, SECRET), "X-Kaveri-Delivery": "d1"})
    assert good.status_code == 202 and good.json()["status"] == "ignored"


def test_payroll_ticket_is_never_autonomous_end_to_end(wired):
    mock, platform = wired()
    inc = new_incident(mock, short_description="Access to payroll module",
                       description="need PeopleDesk payroll module access, new HR coordinator", u_vip="true")
    d = platform.get(f"/decisions/{inc['sys_id']}").json()["decision"]
    assert d["level"] in ("A0", "A1") and d["owner"] == "fin-hr-apps"
    assert "vip-requester-assist-only" in json.dumps(d["steps"])


def test_policy_version_is_audited_at_startup(wired):
    _, platform = wired()
    health = platform.get("/health").json()
    assert health["policy"] == "2026.11-pilot" and len(health["policy_version"]) == 12
    assert platform.get("/audit/verify").json()["events"] == 1  # policy.loaded
