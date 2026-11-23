"""Hand-written redaction cases, including formats the synthetic generator never produces."""

import json

import pytest

from hao.config import REPO_ROOT
from hao.evals.redaction_eval import evaluate_redaction
from hao.intake.redaction import Redactor, Vault


@pytest.fixture
def r():
    return Redactor()


@pytest.mark.parametrize("text,secret", [
    ("call me on +91 9840012345", "9840012345"),
    ("mob: 98400 12345 anytime", "98400 12345"),
    ("whatsapp +91-98400-12345", "98400-12345"),
    ("reach me at 09840012345", "09840012345"),
    ("my id is KVR-48213 pls check", "KVR-48213"),
    ("Employee ID: TMP-2231", "TMP-2231"),
    ("mail ravi.k@gmail.com or ravi@kaveri.example", "ravi.k@gmail.com"),
    ("PAN ABCDE1234F for the reimbursement", "ABCDE1234F"),
    ("aadhaar 1234 5678 9012", "1234 5678 9012"),
])
def test_structured_pii_formats(r, text, secret):
    assert secret not in r.redact(text).text


def test_does_not_redact_operational_numbers(r):
    text = "Error WMS-4021 on dock 4, 14 trucks waiting, timeout after 300s, ORA-01017"
    assert r.redact(text).text == text


def test_caller_name_and_first_name_in_body(r):
    res = r.redact("Hi, this is Anita. Anita Manda from Chennai ops.", known_names=["Anita Manda"])
    assert "Anita" not in res.text and "Manda" not in res.text


def test_signature_name_found_without_context(r):
    text = "Please help.\nRegards,\nKarthik Subramanian\nPlanner | Kaveri Logistics, Chennai\nMob: +91 9876543210"
    out = r.redact(text).text
    assert "Karthik" not in out and "9876543210" not in out
    assert "Planner | Kaveri Logistics, Chennai" in out  # role and site are useful context, kept


def test_signature_through_html_remnants(r):
    text = "Thanks<br>Meera Iyer<br/>\nAnalyst | Kaveri Logistics, Mumbai<br>Emp ID: KVR-11111"
    out = r.redact(text).text
    assert "Meera" not in out and "KVR-11111" not in out


def test_forwarded_header_even_when_mangled(r):
    out = r.redact("Fom: Dev Shetty <dev.shetty@kaveri.example>\nSubject: RE").text
    assert "Dev Shetty" not in out and "dev.shetty" not in out


def test_directory_catches_names_with_no_other_signal():
    text = "please ask Suresh in accounts, he knows"
    assert "Suresh" in Redactor().redact(text).text  # known gap without a directory
    assert "Suresh" not in Redactor(directory=["Suresh Babu"]).redact(text).text


def test_tokens_are_consistent_and_reversible(r):
    vault = Vault()
    a = r.redact("From Ravi Kumar <ravi@kaveri.example>", known_names=["Ravi Kumar"], vault=vault)
    b = r.redact("Ravi Kumar again, ravi@kaveri.example", known_names=["Ravi Kumar"], vault=vault)
    assert "<PERSON_1>" in a.text and "<PERSON_1>" in b.text
    assert "<EMAIL_1>" in a.text and "<EMAIL_1>" in b.text
    assert vault.reveal(b.text) == "Ravi Kumar again, ravi@kaveri.example"


def test_redact_ticket_tokenises_metadata(r):
    ticket = {"caller_id": "Anita Manda", "caller_email": "anita.manda@kaveri.example",
              "short_description": "Anita: vpn down", "description": "VPN not connecting. Anita"}
    red, _, vault = r.redact_ticket(ticket)
    assert "Anita" not in json.dumps(red)
    assert red["caller_id"] == "<PERSON_1>"
    # One person, one token: reveal shows the canonical full name to the human reviewer.
    assert vault.reveal(red["short_description"]) == "Anita Manda: vpn down"


def test_redaction_eval_on_committed_sample():
    def read(name):
        return [json.loads(x) for x in (REPO_ROOT / "data" / "synthetic" / name).read_text().splitlines()]

    rep = evaluate_redaction(read("sample_tickets.jsonl"), read("sample_labels.jsonl"))
    assert rep.recall["ALL"] >= 0.99
    assert rep.precision >= 0.95
    assert rep.utility >= 0.99
