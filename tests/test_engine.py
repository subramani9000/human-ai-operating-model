"""Policy engine: worked examples plus property-based safety invariants.

The invariants are checked against the real policy *and* against the most permissive policy the
validator allows (every category promoted to its max_level), so they hold after any future promotion.
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hao.config import REPO_ROOT
from hao.policy.engine import DecisionInput, decide
from hao.policy.schema import Level, load_policy

POLICY = load_policy(REPO_ROOT / "policy")


def most_permissive(policy):
    cats = {k: c.model_copy(update={"current_level": c.max_level}) for k, c in policy.categories.items()}
    return policy.model_copy(update={"categories": cats})


PERMISSIVE = most_permissive(POLICY)
CATEGORIES = [*POLICY.categories, "unknown", "made_up_category"]
APPS = [*POLICY.applications, "", "SomeNewApp"]

inputs = st.builds(
    DecisionInput,
    category=st.sampled_from(CATEGORIES),
    application=st.sampled_from(APPS),
    confidence=st.floats(min_value=0, max_value=1, allow_nan=False),
    priority=st.sampled_from(["1", "2", "3", "4", "5"]),
    requester_is_vip=st.booleans(),
    reopen_count=st.integers(min_value=0, max_value=5),
)
policies = st.sampled_from([POLICY, PERMISSIVE])


# ----------------------------------------------------------------------------- worked examples

def test_password_reset_confident_is_category_default():
    d = decide(PERMISSIVE, DecisionInput("password_reset", "SSO", confidence=0.95, priority="3"))
    assert d.level == Level.A3 and d.owner_name == "Priya Nair" and d.assignment_group == "SD-L1"
    assert d.applied_rules == []


def test_password_reset_on_payroll_app_is_capped_by_application():
    d = decide(PERMISSIVE, DecisionInput("password_reset", "PeopleDesk", confidence=0.95))
    assert d.level == Level.A2
    assert "application PeopleDesk cap → A2" in d.explain()


def test_low_confidence_downgrades_one_level():
    d = decide(PERMISSIVE, DecisionInput("password_reset", "SSO", confidence=0.7))
    assert d.level == Level.A2 and d.applied_rules == ["low-confidence-downgrade"]


def test_very_low_confidence_goes_to_human():
    d = decide(PERMISSIVE, DecisionInput("password_reset", "SSO", confidence=0.3))
    assert d.level == Level.A0
    assert set(d.applied_rules) >= {"very-low-confidence-human"}


def test_unknown_category_goes_to_l1_human():
    d = decide(POLICY, DecisionInput("unknown", confidence=0.2))
    assert d.level == Level.A0 and d.category == "unknown" and d.owner == "l1-lead"


def test_decision_carries_policy_version_for_audit():
    d = decide(POLICY, DecisionInput("vpn_access", "GlobalVPN", 0.9))
    assert d.policy_version == POLICY.version and d.policy_label == "2026.11-pilot"


def test_explanation_is_human_readable():
    d = decide(PERMISSIVE, DecisionInput("password_reset", "SSO", confidence=0.7, requester_is_vip=True))
    assert d.explain().startswith("A1 (AI assists, human leads).")
    assert "rule vip-requester-assist-only → A1" in d.explain()


# ----------------------------------------------------------------------------- invariants

@settings(max_examples=600, deadline=None)
@given(x=inputs, policy=policies)
def test_no_step_ever_raises_the_level(x, policy):
    for s in decide(policy, x).steps:
        assert Level(s["to"]).rank <= Level(s["from"]).rank


@settings(max_examples=600, deadline=None)
@given(x=inputs, policy=policies)
def test_never_above_category_or_application_ceiling(x, policy):
    d = decide(policy, x)
    cat = policy.categories.get(x.category)
    assert d.level <= (cat.max_level if cat else Level.A0)
    if x.application in policy.applications:
        assert d.level <= policy.applications[x.application].max_level


@settings(max_examples=600, deadline=None)
@given(x=inputs, policy=policies)
def test_finance_and_hr_systems_never_autonomous(x, policy):
    if x.application in ("FinCore", "PeopleDesk") or x.category in (
            "payroll_access", "payroll_discrepancy", "expense_claim_issue"):
        assert decide(policy, x).level <= Level.A2


@settings(max_examples=600, deadline=None)
@given(x=inputs, policy=policies)
def test_critical_work_and_p1_are_human_only(x, policy):
    d = decide(policy, x)
    if x.category in ("security_phishing", "network_outage") or x.application == "CorpNetwork" or x.priority == "1":
        assert d.level == Level.A0


@settings(max_examples=600, deadline=None)
@given(x=inputs, policy=policies)
def test_vip_reopened_and_low_confidence_caps(x, policy):
    d = decide(policy, x)
    if x.requester_is_vip or x.reopen_count >= 1:
        assert d.level <= Level.A1
    if x.confidence < 0.5:
        assert d.level == Level.A0


@settings(max_examples=400, deadline=None)
@given(x=inputs, policy=policies, c2=st.floats(min_value=0, max_value=1, allow_nan=False))
def test_more_confidence_never_means_less_oversight_is_violated(x, policy, c2):
    """Monotonic: raising confidence can raise autonomy, lowering it can never raise autonomy."""
    lo, hi = sorted([x.confidence, c2])
    a = decide(policy, DecisionInput(x.category, x.application, lo, x.priority, x.requester_is_vip, x.reopen_count))
    b = decide(policy, DecisionInput(x.category, x.application, hi, x.priority, x.requester_is_vip, x.reopen_count))
    assert a.level <= b.level


@pytest.mark.parametrize("category", list(POLICY.categories))
def test_every_category_has_an_accountable_owner(category):
    d = decide(POLICY, DecisionInput(category, confidence=0.99))
    assert d.owner in POLICY.owners and d.owner_name


def test_security_tripwire_catches_confident_misclassification():
    """The classifier says 'password reset' at 0.95; the text says phishing. The text wins."""
    x = DecisionInput("password_reset", "SSO", confidence=0.95,
                      text="got weird mail asking password, i clicked the link")
    d = decide(PERMISSIVE, x)
    assert d.level == Level.A0 and "security-tripwire" in d.applied_rules


def test_outage_tripwire():
    d = decide(PERMISSIVE, DecisionInput("app_error", "KLMS", 0.95, text="Whole site is offline since 10am"))
    assert d.level == Level.A0 and "outage-tripwire" in d.applied_rules
