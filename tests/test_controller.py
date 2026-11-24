import shutil
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from hao.config import REPO_ROOT
from hao.controller import autonomy as ctl
from hao.db.models import HaoAuditEvent, HaoAutonomyChange, HaoFeedback, HaoWorkItem
from hao.db.session import init_db, make_engine, make_session_factory
from hao.policy.engine import DecisionInput, decide
from hao.policy.schema import Level, load_policy

POLICY = load_policy(REPO_ROOT / "policy")
NOW = datetime.now(UTC)


@pytest.fixture
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'c.db'}")
    init_db(engine, prefix="hao_")
    with make_session_factory(engine)() as s:
        yield s


_next = iter(range(1, 100000))


def fb(session, category, verdict, *, item=None, reason="", age_days=0.0, level="A2"):
    item = item if item is not None else next(_next)
    session.add(HaoFeedback(
        decision_id=1, work_item_id=item, category=category, level=level, reviewer="r@kaveri.example",
        verdict=verdict, reason_code=reason, corrected_category="", corrected_group="", note="",
        created_at=NOW - timedelta(days=age_days, seconds=1)))
    session.commit()
    return item


def test_evidence_is_per_item_and_negative_wins(session):
    a = fb(session, "password_reset", "approved")
    fb(session, "password_reset", "reopened", item=a)  # approved, then the user reopened it
    fb(session, "password_reset", "approved")
    fb(session, "password_reset", "qa_pass")
    fb(session, "password_reset", "rejected", reason="wrong_category")
    ev = ctl.evidence(session, POLICY)["password_reset"]
    assert (ev.items, ev.positive, ev.negative) == (4, 2, 2)
    assert ev.verdicts == {"approved": 2, "reopened": 1, "qa_pass": 1, "rejected": 1}


def test_old_evidence_falls_out_of_the_window(session):
    fb(session, "password_reset", "rejected", reason="tone", age_days=20)
    fb(session, "password_reset", "approved", age_days=1)
    ev = ctl.evidence(session, POLICY)["password_reset"]
    assert ev.items == 1 and ev.precision == 1.0


A3_POLICY = POLICY.model_copy(update={"categories": {
    **POLICY.categories,
    "password_reset": POLICY.categories["password_reset"].model_copy(update={"current_level": Level.A3})}})


def executions(session, category, n):
    ids = []
    for _ in range(n):
        w = HaoWorkItem(incident_sys_id="x", decision_id=1, level="A3", lane="auto", state="executed",
                        category=category, plan=[], draft={}, context={}, results=[], error="", reviewer="",
                        created_at=NOW - timedelta(hours=1))
        session.add(w)
        session.commit()
        ids.append(w.id)
    return ids


def test_demotes_a3_on_qa_precision_drop_once(session):
    for _ in range(8):
        fb(session, "password_reset", "qa_pass", level="A3")
    for _ in range(2):
        fb(session, "password_reset", "qa_fail", reason="wrong_action", level="A3")
    d = ctl.check_demotion(session, A3_POLICY, "password_reset")
    assert d["from"] == "A3" and d["to"] == "A2" and "QA precision 80.0%" in d["reason"]
    caps = ctl.load_caps(session)
    assert decide(A3_POLICY, DecisionInput("password_reset", "SSO", 0.99), caps).level == Level.A2
    assert ctl.check_demotion(session, A3_POLICY, "password_reset") is None  # same evidence can't demote twice
    assert session.scalar(select(HaoAuditEvent).where(HaoAuditEvent.action == "autonomy.demoted"))


def test_low_precision_at_a2_blocks_promotion_but_keeps_reviews(session):
    for _ in range(8):
        fb(session, "password_reset", "approved")
    for _ in range(4):
        fb(session, "password_reset", "rejected", reason="incorrect_information")
    assert ctl.check_demotion(session, POLICY, "password_reset") is None
    assert ctl.run(session, POLICY).proposed == []


def test_too_little_evidence_does_not_demote(session):
    fb(session, "password_reset", "approved")
    fb(session, "password_reset", "rejected", reason="tone")
    assert ctl.check_demotion(session, POLICY, "password_reset") is None


def test_single_unsafe_verdict_demotes_immediately(session):
    fb(session, "account_unlock", "qa_fail", reason="unsafe")
    d = ctl.check_demotion(session, POLICY, "account_unlock")
    assert d["to"] == "A1" and "unsafe" in d["reason"]


def test_never_demotes_below_a0(session):
    for _ in range(12):
        fb(session, "security_phishing", "rejected", reason="unsafe")
    assert ctl.check_demotion(session, POLICY, "security_phishing") is None


def test_promotion_is_proposed_not_applied(session):
    for _ in range(49):
        fb(session, "password_reset", "approved")
    fb(session, "password_reset", "edited", reason="tone")  # 98% over 50 items
    r = ctl.run(session, POLICY)
    assert r.demoted == [] and len(r.proposed) == 1
    p = r.proposed[0]
    assert (p["category"], p["from"], p["to"], p["owner"]) == ("password_reset", "A2", "A3", "Priya Nair")
    assert ctl.run(session, POLICY).proposed == []  # no duplicate proposal
    assert decide(POLICY, DecisionInput("password_reset", "SSO", 0.99), ctl.load_caps(session)).level == Level.A2


def test_no_promotion_below_threshold_or_at_ceiling(session):
    for _ in range(45):
        fb(session, "password_reset", "approved")
    for _ in range(5):
        fb(session, "password_reset", "rejected", reason="tone")  # 90%: neither promote nor demote
    for _ in range(60):
        fb(session, "security_phishing", "approved")  # already at its max (A0)
    r = ctl.run(session, POLICY)
    assert r.proposed == [] and r.demoted == []


def test_only_the_owner_can_apply_and_it_edits_the_policy(session, tmp_path):
    policy_dir = tmp_path / "policy"
    shutil.copytree(REPO_ROOT / "policy", policy_dir)
    for _ in range(50):
        fb(session, "password_reset", "approved")
    pid = ctl.run(session, POLICY).proposed[0]["proposal"]
    with pytest.raises(ctl.ControllerError, match="only the category owner"):
        ctl.apply_promotion(session, POLICY, pid, "someone@kaveri.example", policy_dir=policy_dir)
    ch = ctl.apply_promotion(session, POLICY, pid, "priya.nair@kaveri.example", policy_dir=policy_dir)
    assert ch.status == "applied" and ch.applied_at is not None
    new = load_policy(policy_dir)
    assert new.categories["password_reset"].current_level == Level.A3
    assert new.categories["account_unlock"].current_level == Level.A2  # nothing else touched
    assert "# Kaveri Logistics" in (policy_dir / "autonomy.yaml").read_text()  # comments preserved
    # evidence restarts at the new level: A2-era approvals don't count for A3
    assert ctl.evidence(session, new)["password_reset"].items == 0
    with pytest.raises(ctl.ControllerError):
        ctl.apply_promotion(session, POLICY, pid, "priya.nair@kaveri.example", policy_dir=policy_dir)


def test_owner_clears_demotion(session):
    fb(session, "account_unlock", "qa_fail", reason="unsafe")
    ctl.check_demotion(session, POLICY, "account_unlock")
    with pytest.raises(ctl.ControllerError):
        ctl.clear_override(session, POLICY, "account_unlock", "random@kaveri.example")
    assert ctl.clear_override(session, POLICY, "account_unlock", "priya.nair@kaveri.example", "fixed playbook") == 1
    assert ctl.load_caps(session) == {}
    status = {r["category"]: r for r in ctl.status(session, POLICY)}
    assert status["account_unlock"]["effective_level"] == "A2" and status["account_unlock"]["items"] == 0


def test_demoted_category_is_not_proposed_for_promotion(session):
    for _ in range(9):
        fb(session, "how_to_question", "approved")
    fb(session, "how_to_question", "rejected", reason="unsafe")  # unsafe demotes at any level
    ctl.check_demotion(session, POLICY, "how_to_question")
    for _ in range(60):
        fb(session, "how_to_question", "approved")
    assert ctl.run(session, POLICY).proposed == []
    assert session.scalars(select(HaoAutonomyChange)).all() == []


def test_minor_edits_count_as_correct_work(session):
    fb(session, "password_reset", "edited", reason="tone")
    fb(session, "password_reset", "edited", reason="incorrect_information")
    ev = ctl.evidence(session, POLICY)["password_reset"]
    assert (ev.items, ev.positive, ev.negative) == (2, 1, 1)


def test_reopens_of_unsampled_work_do_not_bias_precision(session):
    """100 executions, a 20% QA sample all passing, 5 reopens among the unsampled 80: that's 95% good work.
    Counting the reopens in precision would read 20/25 = 80% and wrongly demote (the bug the pilot found)."""
    ids = executions(session, "password_reset", 100)
    for i in ids[:20]:
        fb(session, "password_reset", "qa_pass", item=i, level="A3")
    for i in ids[20:25]:
        fb(session, "password_reset", "reopened", item=i, level="A3")
    ev = ctl.evidence(session, A3_POLICY)["password_reset"]
    assert ev.precision == 1.0 and ev.items == 20
    assert ev.executions == 100 and ev.reopen_rate == 0.05
    assert ctl.check_demotion(session, A3_POLICY, "password_reset") is None


def test_demotes_a3_on_high_reopen_rate(session):
    ids = executions(session, "password_reset", 20)
    for i in ids[:3]:
        fb(session, "password_reset", "reopened", item=i, level="A3")
    d = ctl.check_demotion(session, A3_POLICY, "password_reset")
    assert d and "reopen rate 15.0%" in d["reason"] and d["to"] == "A2"
