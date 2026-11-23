"""Realistic noise: the mess that makes enterprise text hard, as observed in discovery."""

from __future__ import annotations

import random

KEYBOARD_NEIGHBOURS = {
    "a": "sq", "s": "adw", "d": "sfe", "e": "wrd", "r": "etf", "t": "ryg", "o": "ipl", "i": "uok",
    "n": "bm", "m": "n", "p": "o", "l": "kp", "c": "xv", "g": "fh", "h": "gj", "u": "yi",
}


def add_typos(text: str, rng: random.Random, rate: float = 0.03) -> str:
    """Swap, drop or fat-finger a small share of alphabetic characters."""
    chars = list(text)
    i = 0
    while i < len(chars):
        ch = chars[i]
        if ch.isalpha() and rng.random() < rate:
            op = rng.choice(("swap", "drop", "neighbour"))
            if op == "swap" and i + 1 < len(chars) and chars[i + 1].isalpha():
                chars[i], chars[i + 1] = chars[i + 1], chars[i]
                i += 1
            elif op == "drop":
                chars.pop(i)
                continue
            elif op == "neighbour" and ch.lower() in KEYBOARD_NEIGHBOURS:
                chars[i] = rng.choice(KEYBOARD_NEIGHBOURS[ch.lower()])
        i += 1
    return "".join(chars)


def add_html_remnants(text: str, rng: random.Random) -> str:
    """Email-to-ticket connectors often leave markup behind."""
    text = text.replace("\n\n", rng.choice(("<br><br>", "\n&nbsp;\n", "</p><p>")))
    return text.replace("\n", rng.choice(("<br>", "\n", "<br/>\n")))


def signature(name: str, title: str, phone: str, emp_id: str, city: str, rng: random.Random) -> str:
    """Corporate email signature. Contains PII by design, to exercise redaction."""
    lines = [name, f"{title} | Kaveri Logistics, {city}", f"Mob: {phone}", f"Emp ID: {emp_id}"]
    if rng.random() < 0.3:
        lines.append("Sent from my iPhone")
    if rng.random() < 0.4:
        lines.append("DISCLAIMER: This e-mail and any attachments are confidential and intended solely "
                     "for the addressee.")
    return "\n".join(lines)


def forwarded_chain(body: str, fwd_from: str, fwd_email: str, rng: random.Random) -> str:
    """Wrap a body in one or two 'Fwd:' headers, as when managers forward user emails to the desk."""
    depth = rng.choice((1, 1, 2))
    out = body
    for _ in range(depth):
        out = (f"Please check this.\n\n---------- Forwarded message ---------\nFrom: {fwd_from} <{fwd_email}>\n"
               f"Subject: RE: issue\n\n{out}")
    return out
