"""PII redaction: runs before any text leaves the client boundary (CISO requirement, discovery D5).

Context-aware and deterministic (ADR 0004):

* Regexes for structured PII: emails, Indian mobile numbers, Kaveri employee IDs, PAN, Aadhaar.
* Names found from *context*: the caller on the ticket, an optional employee directory,
  ``From:`` headers in forwarded mail, and corporate signature blocks.
* Consistent tokens per value (``<PERSON_1>`` stays ``<PERSON_1>`` across fields), so the LLM can still
  reason about "who said what" without seeing anyone's name.
* A reversible vault that never leaves the boundary, so humans in the review console can see originals.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

BREAK = r"(?:\n|<br\s*/?>|</p><p>)"

PATTERNS: list[tuple[str, re.Pattern[str], int]] = [
    # (entity type, pattern, capture group holding the PII)
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), 0),
    ("PHONE", re.compile(r"(?<!\d)(?:\+91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}(?!\d)"), 0),
    ("EMP_ID", re.compile(r"\bKVR-\d{5}\b", re.I), 0),
    ("EMP_ID", re.compile(r"\bEmp(?:loyee)?\.?\s*ID\s*[:#-]?\s*([A-Z0-9][A-Z0-9-]{3,})", re.I), 1),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), 0),
    ("AADHAAR", re.compile(r"(?<!\d)\d{4}\s\d{4}\s\d{4}(?!\d)"), 0),
    # "Ravi Kumar <ravi.kumar@...>" in forwarded headers, even when "From:" itself is mangled ("Fom:")
    ("PERSON", re.compile(r"(?:^|[:>\n])[ \t]*([A-Za-z][A-Za-z .'-]{1,60}?)\s*<[\w.+-]+@", re.I), 1),
    # Signature: a short line immediately above "<title> | Kaveri Logistics"
    ("PERSON", re.compile(rf"(?:^|{BREAK})([A-Za-z][A-Za-z .'-]{{1,60}}?)(?:\s*{BREAK})+[^\n<|]*\|\s*kaveri logistics",
                          re.I), 1),
]

# Highest priority wins when spans overlap (structured patterns are more reliable than name heuristics).
PRIORITY = {"EMAIL": 0, "EMP_ID": 1, "PHONE": 2, "PAN": 3, "AADHAAR": 4, "PERSON": 5}


@dataclass(frozen=True)
class Entity:
    type: str
    start: int
    end: int
    token: str


@dataclass
class Vault:
    """token → original value. Lives inside the client boundary only; never sent to an LLM."""

    _by_value: dict[tuple[str, str], str] = field(default_factory=dict)
    _by_token: dict[str, str] = field(default_factory=dict)
    _counts: dict[str, int] = field(default_factory=dict)

    def token_for(self, entity_type: str, value: str) -> str:
        key = (entity_type, _norm(value))
        if key not in self._by_value:
            self._counts[entity_type] = self._counts.get(entity_type, 0) + 1
            token = f"<{entity_type}_{self._counts[entity_type]}>"
            self._by_value[key] = token
            self._by_token[token] = value
        return self._by_value[key]

    def reveal(self, text: str) -> str:
        return re.sub(r"<[A-Z_]+_\d+>", lambda m: self._by_token.get(m.group(0), m.group(0)), text)

    def __len__(self) -> int:
        return len(self._by_token)


@dataclass
class RedactionResult:
    text: str
    entities: list[Entity]


def _norm(value: str) -> str:
    return re.sub(r"[\s-]+", "", value).lower() if any(c.isdigit() for c in value) else value.strip().lower()


def _name_patterns(names: Iterable[str]) -> list[re.Pattern[str]]:
    """Full names first, then individual name parts (≥ 3 letters) so 'Anita' alone is caught too."""
    full, parts = set(), set()
    for name in names:
        name = name.strip()
        if not name:
            continue
        full.add(name)
        parts.update(p for p in name.split() if len(p) >= 3)
    ordered = sorted(full, key=len, reverse=True) + sorted(parts - full, key=len, reverse=True)
    return [re.compile(rf"(?<![\w<]){re.escape(n)}(?![\w>])", re.I) for n in ordered]


class Redactor:
    def __init__(self, directory: Iterable[str] = ()):
        self._directory = list(directory)

    def redact(self, text: str, *, known_names: Iterable[str] = (), vault: Vault | None = None) -> RedactionResult:
        vault = vault if vault is not None else Vault()
        if not text:
            return RedactionResult(text, [])

        spans: list[tuple[int, int, str]] = []
        for etype, pattern, group in PATTERNS:
            for m in pattern.finditer(text):
                start, end = m.span(group)
                value = text[start:end].strip()
                if value and not (etype == "PERSON" and len(value.split()) > 4):
                    spans.append((start, start + len(text[start:end].rstrip()), etype))

        # Narrow heuristic name spans to a known full name when one is inside ("From Ravi Kumar" → "Ravi Kumar").
        known = [n.strip() for n in [*known_names, *self._directory] if n and n.strip()]
        narrowed = []
        for start, end, etype in spans:
            if etype == "PERSON":
                chunk = text[start:end].lower()
                hit = next((n for n in sorted(known, key=len, reverse=True) if n.lower() in chunk), None)
                if hit:
                    offset = chunk.index(hit.lower())
                    start, end = start + offset, start + offset + len(hit)
            narrowed.append((start, end, etype))
        spans = narrowed

        # Names discovered by heuristics become "known" for the rest of the text (e.g. first name in body).
        discovered = [text[s:e] for s, e, t in spans if t == "PERSON"]
        full_names = [*known, *discovered]
        for pattern in _name_patterns(full_names):
            spans.extend((m.start(), m.end(), "PERSON") for m in pattern.finditer(text))

        chosen: list[tuple[int, int, str]] = []
        for s, e, t in sorted(spans, key=lambda x: (PRIORITY[x[2]], -(x[1] - x[0]), x[0])):
            if all(e <= cs or s >= ce for cs, ce, _ in chosen):
                chosen.append((s, e, t))
        chosen.sort()

        out, entities, cursor, shift = [], [], 0, 0
        for s, e, t in chosen:
            value = text[s:e]
            if t == "PERSON":  # "Anita" and "Anita Manda" are the same person, so the same token
                value = next((n for n in full_names if value.lower() == n.lower()
                              or value.lower() in n.lower().split()), value)
            token = vault.token_for(t, value)
            out.append(text[cursor:s])
            out.append(token)
            entities.append(Entity(t, s + shift, s + shift + len(token), token))
            shift += len(token) - (e - s)
            cursor = e
        out.append(text[cursor:])
        return RedactionResult("".join(out), entities)

    def redact_ticket(self, ticket: dict, vault: Vault | None = None) -> tuple[dict, list[Entity], Vault]:
        """Redact the free-text fields of a ticket with one shared vault. Caller name is used as context."""
        vault = vault if vault is not None else Vault()
        names = [ticket.get("caller_id", "")]
        redacted = dict(ticket)
        entities: list[Entity] = []
        for f in ("short_description", "description"):
            res = self.redact(ticket.get(f, ""), known_names=names, vault=vault)
            redacted[f] = res.text
            entities += res.entities
        for f in ("caller_id", "caller_email"):  # metadata is never forwarded to an LLM
            if f in redacted:
                redacted[f] = vault.token_for("PERSON" if f == "caller_id" else "EMAIL", ticket[f]) if ticket[f] else ""
        return redacted, entities, vault
