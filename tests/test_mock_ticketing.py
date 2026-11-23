import json
import random

import httpx
import pytest
from fastapi.testclient import TestClient

from hao.config import REPO_ROOT, Settings
from hao.mock_ticketing.app import create_app
from hao.mock_ticketing.webhooks import DELIVERY_HEADER, SIGNATURE_HEADER, verify

KEY = {"X-API-Key": "test-key"}
BASE = "/api/now/table/incident"


class Receiver:
    """Captures webhooks; can be told to fail the first N calls."""

    def __init__(self, fail_first: int = 0):
        self.calls: list[httpx.Request] = []
        self.fail_first = fail_first

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if len(self.calls) <= self.fail_first:
            return httpx.Response(500, text="receiver down")
        return httpx.Response(200, json={"ok": True})


def make_client(receiver=None, **overrides) -> TestClient:
    settings = Settings(database_url="sqlite://", mock_api_key="test-key", webhook_secret="s3cret", **overrides)
    app = create_app(settings, webhook_transport=httpx.MockTransport(receiver or Receiver()),
                     webhook_backoff_s=0, rng=random.Random(0))
    return TestClient(app)


@pytest.fixture
def client():
    return make_client()


def create(client, **fields):
    body = {"short_description": "VPN not connecting", "caller_id": "Test User", **fields}
    resp = client.post(BASE, json=body, headers=KEY)
    assert resp.status_code == 201, resp.text
    return resp.json()["result"]


def test_requires_api_key(client):
    resp = client.get(BASE)
    assert resp.status_code == 401
    assert resp.json()["status"] == "failure"


def test_create_returns_servicenow_shaped_strings(client):
    inc = create(client, u_vip=True, priority=3)
    assert inc["number"] == "INC0010001"
    assert inc["u_vip"] == "true" and inc["priority"] == "3" and inc["state"] == "1"
    assert len(inc["sys_id"]) == 32
    assert inc["resolved_at"] == ""
    assert "T" not in inc["opened_at"]  # "YYYY-MM-DD HH:MM:SS", no ISO marker


def test_short_description_is_mandatory(client):
    resp = client.post(BASE, json={"description": "no subject"}, headers=KEY)
    assert resp.status_code == 400
    assert resp.json()["error"]["message"] == "Data Policy Exception"


def test_unknown_fields_are_silently_ignored(client):
    inc = create(client, assignmentgroup="NET-Connectivity")  # typo'd field name
    assert inc["assignment_group"] == "SD-L1"


def test_resolve_requires_close_code_and_notes(client):
    inc = create(client)
    resp = client.patch(f"{BASE}/{inc['sys_id']}", json={"state": "6"}, headers=KEY)
    assert resp.status_code == 400
    assert "mandatory" in resp.json()["error"]["detail"]
    ok = client.patch(f"{BASE}/{inc['sys_id']}", headers=KEY,
                      json={"state": "6", "close_code": "Solved (Permanently)", "close_notes": "Reset done"})
    assert ok.status_code == 200
    assert ok.json()["result"]["resolved_at"] != ""


def test_reopen_increments_counter_and_closed_is_immutable(client):
    sys_id = create(client)["sys_id"]
    client.patch(f"{BASE}/{sys_id}", headers=KEY, json={"state": "6", "close_code": "x", "close_notes": "y"})
    reopened = client.patch(f"{BASE}/{sys_id}", headers=KEY, json={"state": "2"}).json()["result"]
    assert reopened["reopen_count"] == "1" and reopened["resolved_at"] == ""
    client.patch(f"{BASE}/{sys_id}", headers=KEY, json={"state": "7"})
    assert client.patch(f"{BASE}/{sys_id}", headers=KEY, json={"priority": "1"}).status_code == 403


def test_work_notes_journal(client):
    sys_id = create(client)["sys_id"]
    client.patch(f"{BASE}/{sys_id}", headers={**KEY, "X-Actor": "ai:triage"},
                 json={"work_notes": "Classified as vpn_access (0.93)"})
    notes = client.get(f"{BASE}/{sys_id}/work_notes", headers=KEY).json()["result"]
    assert notes == [{"kind": "work_note", "author": "ai:triage", "note": "Classified as vpn_access (0.93)",
                      "created_at": notes[0]["created_at"]}]


def test_encoded_query_filters_and_orders(client):
    create(client, priority="4", assignment_group="SD-L1")
    create(client, priority="1", assignment_group="NET-Major-Incident")
    create(client, priority="2", assignment_group="SD-L1")
    rows = client.get(BASE, headers=KEY, params={"sysparm_query": "assignment_group=SD-L1^ORDERBYpriority"})
    assert [r["priority"] for r in rows.json()["result"]] == ["2", "4"]
    rows = client.get(BASE, headers=KEY, params={"sysparm_query": "priorityIN1,2"})
    assert len(rows.json()["result"]) == 2
    bad = client.get(BASE, headers=KEY, params={"sysparm_query": "nonsense=1"})
    assert bad.status_code == 400


def test_webhook_is_signed_and_verifiable():
    receiver = Receiver()
    client = make_client(receiver)
    client.post("/api/x_kaveri/webhooks", json={"url": "https://platform.example/hooks"}, headers=KEY)
    inc = create(client)
    assert len(receiver.calls) == 1
    req = receiver.calls[0]
    assert verify(req.content, req.headers[SIGNATURE_HEADER], "s3cret")
    assert not verify(req.content, req.headers[SIGNATURE_HEADER], "wrong-secret")
    payload = json.loads(req.content)
    assert payload["event"] == "incident.created"
    assert payload["incident"]["sys_id"] == inc["sys_id"]


def test_webhook_retries_until_receiver_recovers():
    receiver = Receiver(fail_first=2)
    client = make_client(receiver)
    client.post("/api/x_kaveri/webhooks", json={"url": "https://platform.example/hooks"}, headers=KEY)
    create(client)
    deliveries = client.get("/api/x_kaveri/webhooks/deliveries", headers=KEY).json()["result"]
    assert [d["attempt"] for d in reversed(deliveries)] == ["1", "2", "3"]
    assert [d["success"] for d in reversed(deliveries)] == ["false", "false", "true"]


def test_webhooks_are_at_least_once():
    receiver = Receiver()
    client = make_client(receiver, mock_webhook_duplicate_rate=1.0)
    client.post("/api/x_kaveri/webhooks", json={"url": "https://platform.example/hooks"}, headers=KEY)
    create(client)
    assert len(receiver.calls) == 2
    assert receiver.calls[0].headers[DELIVERY_HEADER] == receiver.calls[1].headers[DELIVERY_HEADER]


def test_chaos_injects_outages():
    client = make_client(mock_failure_rate=1.0)
    resp = client.get(BASE, headers=KEY)
    assert resp.status_code == 503
    assert client.get("/health").status_code == 200  # health stays up, like a real load balancer


def test_seed_loads_synthetic_backlog(client):
    sample = REPO_ROOT / "data" / "synthetic" / "sample_tickets.jsonl"
    resp = client.post("/admin/seed", headers=KEY, params={"path": str(sample), "limit": 50})
    assert resp.json()["result"]["loaded"] == "50"
    rows = client.get(BASE, headers=KEY, params={"sysparm_limit": 1000}).json()["result"]
    assert len(rows) == 50
    rest = client.post("/admin/seed", headers=KEY, params={"path": str(sample)})
    assert rest.json()["result"]["loaded"] == "150"  # already-loaded tickets are skipped
    again = client.post("/admin/seed", headers=KEY, params={"path": str(sample)})
    assert again.json()["result"]["loaded"] == "0"  # idempotent
