"""Phase 6: SSO, kill switch, approval timeouts, webhook replay protection and secret rotation."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from test_lanes import (
    PLAYBOOKS,
    POLICY,
    FixedClassifier,
    create,
    identity_events,
    incident,
    items,
    notes,
    promoted,
)

from hao import clock
from hao.config import Settings
from hao.intake.classifiers import KeywordClassifier
from hao.mock_ticketing.webhooks import sign
from hao.platform.app import create_app as create_platform

SSO = {"X-Proxy-Secret": "proxy-s3cret"}


def proxied(e):
    """Re-point the platform at proxy (SSO) mode, keeping its database and wiring."""
    s = e.app.state.settings
    e.app.state.settings = s.model_copy(update={"auth_mode": "proxy", "auth_proxy_secret": "proxy-s3cret"})
    return e


def as_user(email):
    return {**SSO, "X-Auth-Request-Email": email}


# ----------------------------------------------------------------------------- SSO

def test_proxy_mode_requires_a_signed_in_kaveri_user(env):
    e = proxied(env(FixedClassifier("password_reset", "SSO")))
    assert e.platform.get("/console").status_code == 401
    assert e.platform.get("/console", headers={"X-Auth-Request-Email": "priya.nair@kaveri.example"}).status_code == 401
    assert e.platform.get("/console", headers=as_user("someone@gmail.com")).status_code == 403
    page = e.platform.get("/console", headers=as_user("priya.nair@kaveri.example"))
    assert page.status_code == 200 and "Signed in as <b>priya.nair@kaveri.example</b>" in page.text
    assert e.platform.get("/dashboard.json").status_code == 401
    assert e.platform.get("/health").status_code == 200  # health and webhooks are not behind SSO


def test_reviewer_identity_comes_from_sso_not_the_request(env):
    e = proxied(env(FixedClassifier("password_reset", "SSO")))
    sys_id = create(e.mock)
    item_id = e.platform.get("/work-items", headers=as_user("priya.nair@kaveri.example")).json()[0]["id"]
    r = e.platform.post(f"/work-items/{item_id}/review", headers=as_user("priya.nair@kaveri.example"),
                        json={"reviewer": "ceo@kaveri.example", "verdict": "approve"})  # forged field
    assert r.json()["reviewer"] == "priya.nair@kaveri.example"
    assert identity_events(e)[0]["actor"] == "user:priya.nair@kaveri.example"
    assert incident(e, sys_id)["state"] == "6"


def test_admin_endpoints_need_an_admin(env):
    e = proxied(env(FixedClassifier("password_reset", "SSO")))
    assert e.platform.post("/controller/run", headers=as_user("priya.nair@kaveri.example")).status_code == 403
    assert e.platform.post("/controller/run", headers=as_user("ramesh.iyer@kaveri.example")).status_code == 200


# ----------------------------------------------------------------------------- kill switch

def test_kill_switch_stops_ai_actions_and_only_the_sponsor_restarts(env):
    e = env(FixedClassifier("password_reset", "SSO"), policy=promoted(POLICY, password_reset="A3"))
    create(e.mock)
    on = e.platform.post("/killswitch", json={"on": True, "by": "priya.nair@kaveri.example",
                                               "reason": "wrong resets reported by 3 users"})
    assert on.status_code == 200 and on.json()["state"]["cap"] == "A1"
    assert e.platform.post("/killswitch", json={"on": True, "by": "rando@kaveri.example", "reason": "x"}
                           ).status_code == 409

    sys_id = create(e.mock, short_description="Password reset again")
    d = e.platform.get(f"/decisions/{sys_id}").json()["decision"]
    assert d["level"] == "A1" and "kill-switch" in d["applied_rules"]
    assert incident(e, sys_id)["state"] == "1"  # nothing executed
    assert "Kill switch is ON" in e.platform.get("/console").text

    off = e.platform.post("/killswitch", json={"on": False, "by": "priya.nair@kaveri.example"})
    assert off.status_code == 409 and "sponsor" in off.json()["detail"]
    assert e.platform.post("/killswitch", json={"on": False, "by": "ramesh.iyer@kaveri.example"}).json()["on"] is False
    trail = [a["action"] for a in e.platform.get(f"/decisions/{sys_id}").json()["audit_trail"]]
    assert "autonomy.decided" in trail


def test_kill_switch_blocks_pending_approvals(env):
    e = env(FixedClassifier("password_reset", "SSO"))
    create(e.mock)
    item_id = items(e)[0]["id"]
    e.platform.post("/killswitch", json={"on": True, "by": "priya.nair@kaveri.example", "reason": "incident"})
    r = e.platform.post(f"/work-items/{item_id}/review", json={"reviewer": "p@kaveri.example", "verdict": "approve"})
    assert r.status_code == 409 and identity_events(e) == []


# ----------------------------------------------------------------------------- approval timeout

def test_unreviewed_drafts_expire_to_a_human(env):
    e = env(FixedClassifier("password_reset", "SSO"))
    start = datetime.now(UTC)
    clock.set_time(start)
    try:
        sys_id = create(e.mock)
        assert e.platform.post("/sweep").json() == {"expired": []}
        clock.set_time(start + timedelta(hours=4, minutes=1))
        [expired] = e.platform.post("/sweep").json()["expired"]
    finally:
        clock.set_time(None)
    assert items(e)[0]["state"] == "expired"
    assert any(n["note"].startswith("[AI draft expired]") for n in notes(e, sys_id))
    assert identity_events(e) == []


# ----------------------------------------------------------------------------- webhooks

def _platform(**overrides):
    s = Settings(platform_database_url="sqlite://", webhook_secret="new-secret", **overrides)
    return TestClient(create_platform(s, lambda: None, KeywordClassifier(), playbooks=PLAYBOOKS))


def _post(client, secret, sent_at, delivery="d1"):
    body = json.dumps({"event": "incident.updated", "sent_at": sent_at.isoformat(),
                       "incident": {"sys_id": "x" * 32}}).encode()
    return client.post("/webhooks/ticketing", content=body,
                       headers={"X-Kaveri-Signature": sign(body, secret), "X-Kaveri-Delivery": delivery})


def test_rejects_replayed_or_stale_deliveries():
    c = _platform()
    assert _post(c, "new-secret", datetime.now(UTC)).status_code == 202
    stale = _post(c, "new-secret", datetime.now(UTC) - timedelta(minutes=10), delivery="d2")
    assert stale.status_code == 401 and "stale" in stale.json()["detail"]


@pytest.mark.parametrize("previous,expected", [("old-secret", 202), ("", 401)])
def test_secret_rotation_accepts_the_previous_secret_only_when_configured(previous, expected):
    c = _platform(webhook_secret_previous=previous)
    assert _post(c, "old-secret", datetime.now(UTC)).status_code == expected


# ----------------------------------------------------------------------------- eval gate + observability

def test_eval_gate_compare_directions():
    from hao.evals.gate import compare

    base = {"g": {"accuracy": 0.90, "risk_downgrades": 5, "over_autonomy_high_risk": 1}}
    assert all(r["ok"] for r in compare(base, {"g": {"accuracy": 0.897, "risk_downgrades": 5,
                                                     "over_autonomy_high_risk": 1}}, 0.005))
    rows = {r["metric"]: r for r in compare(base, {"g": {"accuracy": 0.88, "risk_downgrades": 6,
                                                         "over_autonomy_high_risk": 0}}, 0.005)}
    assert not rows["g.accuracy"]["ok"] and not rows["g.risk_downgrades"]["ok"]
    assert rows["g.over_autonomy_high_risk"]["ok"] and "improved" in rows["g.over_autonomy_high_risk"]["note"]


def test_repo_baselines_hold():
    from hao.evals.gate import compare, load_baselines, measure

    baseline, tol = load_baselines()
    assert all(r["ok"] for r in compare(baseline, measure("keyword"), tol))


def test_prometheus_metrics_and_structured_logs_without_pii(env):
    import io
    import logging

    from hao import obs

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(obs.JsonFormatter())
    obs.logger.addHandler(handler)
    try:
        e = env(FixedClassifier("password_reset", "SSO"))
        create(e.mock)
    finally:
        obs.logger.removeHandler(handler)
    text = e.platform.get("/metrics/prometheus").text
    assert 'hao_decisions_total{level="A2"} 1' in text and "hao_audit_chain_ok 1" in text
    assert "hao_kill_switch_on 0" in text and "Anita" not in text
    logs = [json.loads(x) for x in buf.getvalue().splitlines()]
    done = next(x for x in logs if x["event"] == "intake.done")
    assert done["level"] == "info" and done["autonomy_level"] == "A2" and done["category"] == "password_reset"
    assert len(done["sys_id"]) == 32 and done["delivery_id"]
    assert "Anita" not in buf.getvalue()
