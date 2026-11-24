"""Agent lanes end to end: mock client system ⇄ platform, in process."""

import random
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from hao.adapters.base import TicketingError
from hao.adapters.servicenow import ServiceNowAdapter
from hao.agents.drafter import ClaudeDrafter, Draft, DraftRequest, TemplateDrafter, check_draft
from hao.agents.lanes import LaneDeps, ReviewError, review
from hao.agents.playbooks import Playbooks, load_playbooks
from hao.agents.retrieval import BM25, Doc
from hao.agents.tools import ToolContext, ToolRefused, run_tool
from hao.config import REPO_ROOT, Settings
from hao.intake.classifiers import Classification
from hao.mock_ticketing.app import create_app as create_mock
from hao.mock_ticketing.kb import KB_ARTICLES
from hao.platform.app import create_app as create_platform
from hao.policy.schema import Level, load_policy

KEY, SECRET = "test-key", "s3cret"
H = {"X-API-Key": KEY}
POLICY = load_policy(REPO_ROOT / "policy")
PLAYBOOKS = load_playbooks(REPO_ROOT / "policy")


def promoted(policy, **levels):
    cats = dict(policy.categories)
    for c, lvl in levels.items():
        cats[c] = cats[c].model_copy(update={"current_level": Level(lvl)})
    return policy.model_copy(update={"categories": cats})


class FixedClassifier:
    """Deterministic classifier so each test controls the decision precisely."""

    name = "fixed"

    def __init__(self, category, application, confidence=0.95):
        self.out = Classification(category=category, application=application, confidence=confidence,
                                  classifier="fixed", signals="test")

    def classify(self, ticket):
        return self.out


@pytest.fixture
def env():
    holder = {}

    def to_platform(request: httpx.Request) -> httpx.Response:
        resp = holder["platform"].post("/webhooks/ticketing", content=request.content, headers=dict(request.headers))
        return httpx.Response(resp.status_code, content=resp.content)

    def build(classifier, policy=POLICY, playbooks=PLAYBOOKS, drafter=None):
        ms = Settings(database_url="sqlite://", mock_api_key=KEY, webhook_secret=SECRET)
        mock = TestClient(create_mock(ms, webhook_transport=httpx.MockTransport(to_platform), webhook_backoff_s=0))
        ps = Settings(platform_database_url="sqlite://", mock_api_key=KEY, webhook_secret=SECRET,
                      platform_actor="ai:hao-intake")
        adapter = lambda: ServiceNowAdapter(mock, KEY, actor="ai:hao-agent", backoff_s=0)  # noqa: E731
        app = create_platform(ps, adapter, classifier, policy=policy, playbooks=playbooks,
                              drafter=drafter or TemplateDrafter(), rng=random.Random(0))
        platform = TestClient(app)
        holder["platform"] = platform
        mock.post("/api/x_kaveri/webhooks", json={"url": "http://platform/hooks"}, headers=H)
        return SimpleNamespace(mock=mock, platform=platform, app=app, adapter=adapter)

    return build


def create(mock, **fields):
    body = {"short_description": "Password reset request for SSO", "caller_id": "Anita Manda",
            "caller_email": "anita.manda@kaveri.example", "priority": "3",
            "description": "My SSO password expired. Please reset.\nAnita Manda\nAnalyst | Kaveri Logistics, Chennai",
            **fields}
    return mock.post("/api/now/table/incident", json=body, headers=H).json()["result"]["sys_id"]


def items(e, **q):
    return e.platform.get("/work-items", params=q).json()


def notes(e, sys_id, kind=None):
    rows = e.mock.get(f"/api/now/table/incident/{sys_id}/work_notes", headers=H).json()["result"]
    return [r for r in rows if kind is None or r["kind"] == kind]


def incident(e, sys_id):
    return e.mock.get(f"/api/now/table/incident/{sys_id}", headers=H).json()["result"]


def identity_events(e):
    return e.mock.get("/api/x_kaveri/identity/events", headers=H).json()["result"]


# ----------------------------------------------------------------------------- A2: draft → human approval

def test_a2_draft_waits_and_nothing_happens_until_approved(env):
    e = env(FixedClassifier("password_reset", "SSO"))
    sys_id = create(e.mock)
    [item] = items(e)
    assert item["level"] == "A2" and item["state"] == "awaiting_approval"
    assert [p["tool"] for p in item["plan"]] == ["identity.reset_password", "customer.reply", "incident.resolve"]
    assert item["plan"][0]["args"] == {"application": "SSO"}  # no PII stored in the plan
    assert "<PERSON_1>" in item["draft"]["reply"] and "Anita" not in str(item)
    assert identity_events(e) == [] and incident(e, sys_id)["state"] == "1"
    assert any("/console/items/" in n["note"] for n in notes(e, sys_id))


def test_a2_approval_executes_under_the_approvers_name(env):
    e = env(FixedClassifier("password_reset", "SSO"))
    sys_id = create(e.mock)
    item_id = items(e)[0]["id"]
    done = e.platform.post(f"/work-items/{item_id}/review",
                           json={"reviewer": "priya.nair@kaveri.example", "verdict": "approve"}).json()
    assert done["state"] == "executed" and done["reviewer"] == "priya.nair@kaveri.example"
    assert identity_events(e) == [{"action": "reset_password", "user_email": "anita.manda@kaveri.example",
                                   "application": "SSO", "actor": "user:priya.nair@kaveri.example",
                                   "incident": sys_id}]
    inc = incident(e, sys_id)
    assert inc["state"] == "6" and "KB0010001" in inc["close_notes"]
    [reply] = notes(e, sys_id, kind="comment")
    assert reply["note"].startswith("Hi Anita Manda,") and reply["author"] == "user:priya.nair@kaveri.example"
    assert e.platform.get("/audit/verify").json()["ok"]


def test_a2_edit_requires_reason_and_uses_human_text(env):
    e = env(FixedClassifier("password_reset", "SSO"))
    sys_id = create(e.mock)
    item_id = items(e)[0]["id"]
    bad = e.platform.post(f"/work-items/{item_id}/review", json={"reviewer": "p@kaveri.example", "verdict": "edit",
                                                                "reply": "Done!"})
    assert bad.status_code == 409 and "reason_code" in bad.json()["detail"]
    ok = e.platform.post(f"/work-items/{item_id}/review",
                         json={"reviewer": "p@kaveri.example", "verdict": "edit", "reason_code": "tone",
                               "reply": "Hi Anita, sorted! Temporary password is on its way by SMS."})
    assert ok.json()["state"] == "executed"
    assert notes(e, sys_id, kind="comment")[0]["note"].startswith("Hi Anita, sorted!")


def test_a2_reject_records_feedback_and_takes_no_action(env):
    e = env(FixedClassifier("password_reset", "SSO"))
    sys_id = create(e.mock)
    item_id = items(e)[0]["id"]
    r = e.platform.post(f"/work-items/{item_id}/review",
                        json={"reviewer": "p@kaveri.example", "verdict": "reject", "reason_code": "wrong_category"})
    assert r.json()["state"] == "rejected"
    assert identity_events(e) == [] and incident(e, sys_id)["state"] == "1"
    again = e.platform.post(f"/work-items/{item_id}/review",
                            json={"reviewer": "p@kaveri.example", "verdict": "approve"})
    assert again.status_code == 409  # can't approve after reject


def test_a2_approval_is_rechecked_against_the_current_policy(env):
    e = env(FixedClassifier("password_reset", "SSO"))
    create(e.mock)
    item_id = items(e)[0]["id"]
    demoted = promoted(POLICY, password_reset="A1")  # owner demoted the category after the draft was made
    adapter = e.adapter()
    deps = LaneDeps(session_factory=e.app.state.session_factory, adapter=adapter, policy=demoted,
                    playbooks=PLAYBOOKS, drafter=TemplateDrafter())
    with pytest.raises(ReviewError, match="can't be executed"):
        review(deps, item_id, reviewer="p@kaveri.example", verdict="approve")
    assert identity_events(e) == []


# ----------------------------------------------------------------------------- A3: act, then QA sample

def test_a3_acts_as_the_agent_and_samples_for_qa(env):
    pbs = PLAYBOOKS.model_copy(update={"qa_sample_rate": 1.0})
    e = env(FixedClassifier("password_reset", "SSO"), policy=promoted(POLICY, password_reset="A3"), playbooks=pbs)
    sys_id = create(e.mock)
    auto, qa = sorted(items(e), key=lambda i: i["id"])
    assert auto["level"] == "A3" and auto["state"] == "executed" and [r["ok"] for r in auto["results"]] == [True] * 3
    assert identity_events(e)[0]["actor"] == "ai:hao-agent"
    assert incident(e, sys_id)["state"] == "6"
    assert qa["lane"] == "qa" and qa["state"] == "qa_pending" and qa["parent_id"] == auto["id"]
    r = e.platform.post(f"/work-items/{qa['id']}/review", json={"reviewer": "qa@kaveri.example", "verdict": "pass"})
    assert r.json()["state"] == "qa_done"


def test_a3_on_payroll_app_is_impossible_even_if_category_is_promoted(env):
    e = env(FixedClassifier("password_reset", "PeopleDesk"), policy=promoted(POLICY, password_reset="A3"))
    create(e.mock)
    [item] = items(e)
    assert item["level"] == "A2" and item["state"] == "awaiting_approval"  # application cap


def test_a3_failure_falls_back_to_human_handoff(env):
    e = env(FixedClassifier("password_reset", "SSO"), policy=promoted(POLICY, password_reset="A3"))
    sys_id = create(e.mock, caller_email="")  # nobody to reset the password for
    [item] = items(e)
    assert item["state"] == "failed" and "caller email" in item["error"]
    assert incident(e, sys_id)["state"] == "1" and identity_events(e) == []
    assert any("[AI handoff packet] Could not act" in n["note"] for n in notes(e, sys_id))


def test_category_without_playbook_never_acts(env):
    e = env(FixedClassifier("email_issue", "Outlook"), policy=promoted(POLICY, email_issue="A3"))
    sys_id = create(e.mock, short_description="Outlook not syncing", description="mails stuck in outbox")
    [item] = items(e)
    assert item["state"] == "handed_off" and item["context"]["plan_problem"] == "no playbook for email_issue"
    assert incident(e, sys_id)["state"] == "1"


# ----------------------------------------------------------------------------- A1 / A0

def test_a1_routes_and_posts_handoff_packet(env):
    e = env(FixedClassifier("vpn_access", "GlobalVPN"))
    sys_id = create(e.mock, short_description="GlobalVPN keeps disconnecting",
                    description="Gateway unreachable every 5 minutes")
    [item] = items(e)
    assert item["level"] == "A1" and item["state"] == "handed_off"
    assert incident(e, sys_id)["assignment_group"] == "NET-Connectivity"
    packet = next(n["note"] for n in notes(e, sys_id) if n["note"].startswith("[AI handoff packet]"))
    assert "KB0010007" in packet and "Suggested next step" in packet
    assert notes(e, sys_id, kind="comment") == []  # nothing customer-facing at A1


def test_a0_takes_no_ai_action_at_all(env):
    e = env(FixedClassifier("security_phishing", "Outlook"))
    sys_id = create(e.mock, short_description="Suspicious email received", assignment_group="SD-L1")
    [item] = items(e)
    assert item["level"] == "A0" and item["state"] == "human_only"
    assert incident(e, sys_id)["assignment_group"] == "SD-L1"  # not even routing


def test_draft_failing_guardrails_degrades_to_handoff(env):
    class LeakyDrafter(TemplateDrafter):
        def draft(self, req):
            d = super().draft(req)
            d.reply += " Call me on +91 9876543210."  # an LLM inventing personal data
            return d

    e = env(FixedClassifier("password_reset", "SSO"), drafter=LeakyDrafter())
    create(e.mock)
    [item] = items(e)
    assert item["state"] == "handed_off"
    assert any("raw personal data" in p for p in item["context"]["guardrail_problems"])


# ----------------------------------------------------------------------------- console

def test_console_lists_and_reviews_via_form(env):
    e = env(FixedClassifier("password_reset", "SSO"))
    sys_id = create(e.mock)
    item_id = items(e)[0]["id"]
    assert "Waiting for you (1)" in e.platform.get("/console").text
    page = e.platform.get(f"/console/items/{item_id}").text
    assert "Anita Manda" in page and "identity.reset_password" in page and "KB0010001" in page
    r = e.platform.post(f"/console/items/{item_id}", content="reviewer=priya%40kaveri.example&verdict=approve",
                        headers={"Content-Type": "application/x-www-form-urlencoded"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/console"
    assert incident(e, sys_id)["state"] == "6"


# ----------------------------------------------------------------------------- units

def test_tool_guards():
    ctx = ToolContext(adapter=None, incident={"sys_id": "x", "caller_email": "a@b"}, level=Level.A3, actor="ai:x")
    allowed = {"identity.reset_password", "customer.reply"}
    with pytest.raises(ToolRefused, match="human approver"):
        run_tool("identity.reset_password", {"application": "PeopleDesk"}, ctx, allowed)
    with pytest.raises(ToolRefused, match="not in this category's playbook"):
        run_tool("incident.resolve", {}, ctx, allowed)
    a2 = ToolContext(adapter=None, incident={"sys_id": "x"}, level=Level.A2, actor="ai:x")
    with pytest.raises(ToolRefused, match="does not allow AI execution"):
        run_tool("customer.reply", {}, a2, allowed)


def test_client_iam_refuses_ai_on_privileged_apps(env):
    e = env(FixedClassifier("unknown", ""))
    with pytest.raises(TicketingError, match="requires a human actor"):
        e.adapter().identity("reset_password", "a@kaveri.example", "PeopleDesk", "x")


def test_bm25_finds_the_right_kb_article():
    index = BM25([Doc(n, t, x, {}) for n, t, x in KB_ARTICLES])
    assert index.search("GlobalVPN gateway unreachable from home")[0][0].id == "KB0010007"
    assert index.search("how do i set out of office automatic reply")[0][0].id == "KB0010005"
    assert index.search("<PERSON_1> <PHONE_1>") == []


def test_check_draft_guardrails():
    ok = Draft(reply="Hi <PERSON_1>, done. See KB0010001 at https://kb.kaveri.example/KB0010001")
    assert check_draft(ok, {"<PERSON_1>"}) == []
    assert any("unknown" in p for p in check_draft(Draft(reply="Hi <PERSON_9>"), {"<PERSON_1>"}))
    assert any("credential" in p for p in check_draft(Draft(reply="Your temporary password is X1!"), set()))
    assert any("links outside" in p for p in check_draft(Draft(reply="see https://evil.example/x"), set()))
    assert any("raw personal" in p for p in check_draft(Draft(reply="mail me at x@gmail.com"), set()))


def test_claude_drafter_parses_and_falls_back():
    def client(payload=None, exc=None):
        def create(**kw):
            if exc:
                raise exc
            return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=payload)],
                                   usage=SimpleNamespace(input_tokens=1200, output_tokens=150))
        return SimpleNamespace(messages=SimpleNamespace(create=create))

    req = DraftRequest(category="password_reset", level="A2", application="SSO",
                       ticket={"caller_id": "<PERSON_1>", "short_description": "pwd"}, actions=["customer.reply"],
                       playbook=PLAYBOOKS.playbooks["password_reset"])
    out = ClaudeDrafter(client({"reply": "Hi <PERSON_1>", "close_notes": "Reset.", "handoff_summary": "s",
                                "next_step": "n"})).draft(req)
    assert out.reply == "Hi <PERSON_1>" and out.model == "claude-sonnet-5-5" and out.input_tokens == 1200
    fallback = ClaudeDrafter(client(exc=RuntimeError("overloaded"))).draft(req)
    assert fallback.drafter == "template-v1" and "overloaded" in fallback.error and fallback.reply


def test_playbooks_validate_against_policy():
    assert PLAYBOOKS.check_against(POLICY) == []
    with pytest.raises(ValueError, match="unknown tools"):
        Playbooks.model_validate({**PLAYBOOKS.model_dump(), "playbooks": {
            "password_reset": {"actions": [{"tool": "database.drop"}], "reply": "x"}}})
    bad = PLAYBOOKS.model_copy(update={"playbooks": {**PLAYBOOKS.playbooks,
                                                     "payroll_discrepancy": PLAYBOOKS.playbooks["how_to_question"]}})
    capped = promoted(POLICY)  # payroll_discrepancy max_level A2 → allowed; network_outage would not be
    assert bad.check_against(capped) == []
    worse = PLAYBOOKS.model_copy(update={"playbooks": {"network_outage": PLAYBOOKS.playbooks["how_to_question"]}})
    assert "can never run" in worse.check_against(POLICY)[0]


def test_credential_check_does_not_flag_normal_prose():
    assert check_draft(Draft(reply="Your temporary password is on its way by SMS."), set()) == []
    assert check_draft(Draft(reply="password: hunter2"), set())


# ----------------------------------------------------------------------------- reopens → evidence → demotion

def reopen(e, sys_id):
    e.mock.patch(f"/api/now/table/incident/{sys_id}", json={"state": "2"}, headers=H)


def test_reopen_of_ai_resolved_ticket_is_negative_feedback(env):
    e = env(FixedClassifier("password_reset", "SSO"), policy=promoted(POLICY, password_reset="A3"))
    sys_id = create(e.mock)
    reopen(e, sys_id)
    e.mock.patch(f"/api/now/table/incident/{sys_id}", json={"work_notes": "looking"}, headers=H)  # more updates
    status = {r["category"]: r for r in e.platform.get("/controller/status").json()}
    assert status["password_reset"]["verdicts"] == {"reopened": 1}  # counted once
    trail = e.platform.get(f"/decisions/{sys_id}").json()["audit_trail"]
    assert "feedback.reopened" in [t["action"] for t in trail]


def test_reopen_of_human_handled_ticket_is_ignored(env):
    e = env(FixedClassifier("vpn_access", "GlobalVPN"))
    sys_id = create(e.mock, short_description="vpn down")
    e.mock.patch(f"/api/now/table/incident/{sys_id}", headers=H,
                 json={"state": "6", "close_code": "Solved", "close_notes": "fixed by L1"})
    reopen(e, sys_id)
    assert all(r["verdicts"] == {} for r in e.platform.get("/controller/status").json())


def test_unsafe_rejection_demotes_the_category_for_new_tickets(env):
    e = env(FixedClassifier("password_reset", "SSO"))
    create(e.mock)
    item = items(e)[0]
    e.platform.post(f"/work-items/{item['id']}/review",
                    json={"reviewer": "p@kaveri.example", "verdict": "reject", "reason_code": "unsafe"})
    status = {r["category"]: r for r in e.platform.get("/controller/status").json()}
    assert status["password_reset"]["demoted"] and status["password_reset"]["effective_level"] == "A1"
    new = create(e.mock, short_description="Password reset again")
    d = e.platform.get(f"/decisions/{new}").json()["decision"]
    assert d["level"] == "A1" and "controller-demotion" in d["applied_rules"]


def test_a3_quality_drop_is_caught_by_the_nightly_run(env):
    pbs = PLAYBOOKS.model_copy(update={"qa_sample_rate": 1.0})
    e = env(FixedClassifier("password_reset", "SSO"), policy=promoted(POLICY, password_reset="A3"), playbooks=pbs)
    for i in range(10):
        create(e.mock, short_description=f"Password reset request {i}")
    for n, qa in enumerate(i for i in items(e) if i["lane"] == "qa"):
        verdict = {"verdict": "fail", "reason_code": "wrong_action"} if n < 2 else {"verdict": "pass"}
        e.platform.post(f"/work-items/{qa['id']}/review", json={"reviewer": "qa@kaveri.example", **verdict})
    # The failures came first, when 2 items were too little evidence to act on. The nightly run sees all
    # 10 items (8/10 = 80% < 90%) and demotes A3 → A2: humans approve every action again.
    run = e.platform.post("/controller/run").json()
    assert run["demoted"][0]["category"] == "password_reset" and run["demoted"][0]["to"] == "A2"


def test_dashboard_reports_against_pilot_targets(env):
    pbs = PLAYBOOKS.model_copy(update={"qa_sample_rate": 1.0})
    e = env(FixedClassifier("password_reset", "SSO"), policy=promoted(POLICY, password_reset="A3"), playbooks=pbs)
    s1 = create(e.mock)
    create(e.mock, short_description="Password reset 2")
    reopen(e, s1)
    qa = next(i for i in items(e) if i["lane"] == "qa")
    e.platform.post(f"/work-items/{qa['id']}/review", json={"reviewer": "qa@kaveri.example", "verdict": "pass"})
    d = e.platform.get("/dashboard.json").json()
    assert d["volume"] == {"tickets": 2, "by_level": {"A3": 2, "A2": 0, "A1": 0, "A0": 0}}
    assert d["outcomes"]["ai_resolved"] == 2
    assert d["quality"]["reopened"] == 1 and d["quality"]["reopen_rate_ai_resolved"] == 0.5
    assert d["governance"]["audit_chain_ok"] and d["governance"]["decisions_with_audit_record"] == 1.0
    assert d["governance"]["human_verdicts_captured"] == 1.0
    # 2 × 8 min saved − 1 QA review × 3 min = 13 min
    assert d["value_estimate"]["human_hours_redirected"] == round(13 / 60, 1)
    targets = {t["metric"]: t for t in d["targets"]}
    assert targets["Reopen rate on AI-resolved tickets"]["met"] is False
    page = e.platform.get("/dashboard").text
    assert "Pilot targets" in page and "Human hours redirected" in page
