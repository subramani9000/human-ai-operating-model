"""Redaction eval: does any known PII survive, and do we destroy useful text?

* **Recall** per PII type: a value counts only if it appears verbatim in the original ticket text
  (typo noise can corrupt it; a corrupted value is not the value any more).
* **Precision**: share of redacted spans that correspond to a labelled PII value.
* **Utility**: share of tickets where the application name in the original text survives redaction.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from hao.intake.redaction import Redactor, Vault, _norm


@dataclass
class RedactionReport:
    n_tickets: int = 0
    found: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    leaked: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    spans_total: int = 0
    spans_true: int = 0
    app_mentions: int = 0
    app_preserved: int = 0
    leak_examples: list[dict] = field(default_factory=list)
    false_positive_examples: list[str] = field(default_factory=list)

    @property
    def recall(self) -> dict[str, float]:
        out = {t: 1 - self.leaked[t] / n for t, n in sorted(self.found.items()) if n}
        total = sum(self.found.values())
        out["ALL"] = 1 - sum(self.leaked.values()) / total if total else 1.0
        return out

    @property
    def precision(self) -> float:
        return self.spans_true / self.spans_total if self.spans_total else 1.0

    @property
    def utility(self) -> float:
        return self.app_preserved / self.app_mentions if self.app_mentions else 1.0


def evaluate_redaction(tickets: list[dict], labels: list[dict], redactor: Redactor | None = None) -> RedactionReport:
    redactor = redactor or Redactor()
    rep = RedactionReport(n_tickets=len(tickets))
    for ticket, label in zip(tickets, labels, strict=True):
        original = f"{ticket['short_description']}\n{ticket['description']}"
        vault = Vault()
        redacted_ticket, _, vault = redactor.redact_ticket(ticket, vault)
        redacted = f"{redacted_ticket['short_description']}\n{redacted_ticket['description']}"
        low_orig, low_red = original.lower(), redacted.lower()

        values = {(v["type"], v["value"]) for v in label.get("pii_values", [])}
        for ptype, value in values:
            if value.lower() not in low_orig:
                continue
            rep.found[ptype] += 1
            if value.lower() in low_red:
                rep.leaked[ptype] += 1
                if len(rep.leak_examples) < 10:
                    rep.leak_examples.append({"number": ticket["number"], "type": ptype, "value": value})

        norm_values = [_norm(v) for _, v in values]
        for token, original_value in vault._by_token.items():
            if original_value in (ticket.get("caller_id"), ticket.get("caller_email")):
                continue  # metadata fields, always tokenised by design
            occurrences = redacted.count(token)
            if not occurrences:
                continue
            rep.spans_total += occurrences
            ov = _norm(original_value)
            # Fuzzy match: typo noise corrupts PII ("ganag.pandey@") and redacting it is still correct.
            candidates = [*norm_values, _norm(ticket.get("caller_id", ""))]
            if any(c and (ov in c or SequenceMatcher(None, ov, c).ratio() >= 0.75) for c in candidates):
                rep.spans_true += occurrences
            elif len(rep.false_positive_examples) < 10:
                rep.false_positive_examples.append(f"{ticket['number']}: {original_value!r} → {token}")

        app = label["true_application"].lower()
        if app in low_orig:
            rep.app_mentions += 1
            rep.app_preserved += app in low_red
    return rep
