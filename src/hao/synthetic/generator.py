"""Deterministic synthetic ticket generator for the Kaveri service desk.

Produces two aligned streams:

* ``tickets`` — what the client ticketing system "sees" (messy, ServiceNow-shaped, string-typed)
* ``labels``  — ground truth used only by evals (never shown to the system under test)
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from faker import Faker

from hao.synthetic import noise
from hao.synthetic.catalog import (
    APP_ERRORS,
    CATALOG,
    CITIES,
    SOFTWARE,
    STYLE_WEIGHTS,
    STYLES,
    USER_CATEGORIES,
    CategorySpec,
)

IST_OFFSET = timedelta(hours=5, minutes=30)
TITLES = ("Operations Executive", "Warehouse Supervisor", "Fleet Coordinator", "Analyst", "Finance Associate",
          "HR Coordinator", "Customs Executive", "Planner", "Team Lead", "Manager")
CONTACT_TYPES = (("email", 0.45), ("self-service", 0.35), ("chat", 0.12), ("phone", 0.08))


@dataclass(frozen=True)
class Employee:
    name: str
    email: str
    personal_email: str
    phone: str
    emp_id: str
    title: str
    city: str
    vip: bool


@dataclass
class GeneratorConfig:
    n: int = 5000
    seed: int = 42
    days: int = 30
    end_date: datetime = datetime(2026, 11, 1, tzinfo=UTC)
    n_employees: int = 800
    # Noise rates, calibrated to discovery observations
    p_blank_user_category: float = 0.40
    p_other_user_category: float = 0.15
    p_wrong_user_category: float = 0.10
    p_cmdb_ci_set: float = 0.45
    p_cmdb_ci_wrong: float = 0.05
    p_lands_on_default_group: float = 0.70
    p_wrong_group: float = 0.15
    p_duplicate: float = 0.03
    p_typos: float = 0.35
    p_html: float = 0.12
    p_signature: float = 0.55
    p_forwarded: float = 0.10
    p_empty_description: float = 0.04
    p_lowercase: float = 0.15


def _weighted(rng: random.Random, items, weights):
    return rng.choices(items, weights=weights, k=1)[0]


def _sys_id(rng: random.Random) -> str:
    return hashlib.md5(str(rng.random()).encode()).hexdigest()  # 32-hex, ServiceNow-like


def _build_employees(cfg: GeneratorConfig, rng: random.Random) -> list[Employee]:
    fake = Faker("en_IN")
    fake.seed_instance(cfg.seed)
    people: list[Employee] = []
    seen: set[str] = set()
    while len(people) < cfg.n_employees:
        first, last = fake.first_name(), fake.last_name()
        handle = f"{first}.{last}".lower().replace(" ", "")
        if handle in seen:
            continue
        seen.add(handle)
        people.append(
            Employee(
                name=f"{first} {last}",
                email=f"{handle}@kaveri.example",
                personal_email=f"{first.lower()}{rng.randint(10, 99)}@gmail.com",
                phone=f"+91 {rng.choice('6789')}{rng.randint(100000000, 999999999)}",
                emp_id=f"KVR-{rng.randint(10000, 99999)}",
                title=rng.choice(TITLES),
                city=rng.choice(CITIES),
                vip=rng.random() < 0.02,
            )
        )
    return people


def _timestamp(cfg: GeneratorConfig, rng: random.Random, month_end_bias: float) -> datetime:
    """Business-hours-weighted IST timestamp; Monday and month-end peaks."""
    while True:
        day = rng.randrange(cfg.days)
        date = (cfg.end_date - timedelta(days=cfg.days - day)).date()
        weight = 1.0
        if date.weekday() == 0:
            weight *= 1.4
        elif date.weekday() >= 5:
            weight *= 0.25
        if date.day >= 28 or date.day <= 1:
            weight *= month_end_bias
        if rng.random() < weight / 3.0:
            break
    hour = _weighted(rng, list(range(24)),
                     [0.2] * 7 + [1, 3, 6, 6, 5, 4, 4, 5, 5, 4, 3, 2] + [1, 0.6, 0.4, 0.3, 0.2])
    local = datetime(date.year, date.month, date.day, hour, rng.randrange(60), rng.randrange(60))
    return (local - IST_OFFSET).replace(tzinfo=UTC)


def _fill(template: str, spec: CategorySpec, app: str, city: str, rng: random.Random) -> str:
    return template.format(
        app=app,
        city=city,
        software=rng.choice(SOFTWARE),
        error=rng.choice(APP_ERRORS.get(app, ("Unexpected error occurred",))),
    )


def _choose_spec(rng: random.Random, month_end: bool) -> CategorySpec:
    weights = [c.weight * (c.month_end_multiplier if month_end else 1.0) for c in CATALOG]
    return _weighted(rng, CATALOG, weights)


def generate(cfg: GeneratorConfig | None = None) -> tuple[list[dict], list[dict]]:
    cfg = cfg or GeneratorConfig()
    rng = random.Random(cfg.seed)
    employees = _build_employees(cfg, rng)
    all_groups = sorted({c.assignment_group for c in CATALOG})
    all_apps = sorted({a for c in CATALOG for a in c.applications})

    tickets: list[dict] = []
    labels: list[dict] = []

    for _ in range(cfg.n):
        month_end_roll = rng.random() < 0.15
        spec = _choose_spec(rng, month_end_roll)
        emp = rng.choice(employees)
        opened = _timestamp(cfg, rng, spec.month_end_multiplier)
        noise_applied: list[str] = []
        pii_types: list[str] = []
        duplicate_of = None

        # Near-duplicate: same caller re-raises the same issue shortly after (different wording).
        if tickets and rng.random() < cfg.p_duplicate:
            src_idx = rng.randrange(max(0, len(tickets) - 50), len(tickets))
            src_t, src_l = tickets[src_idx], labels[src_idx]
            spec = next(c for c in CATALOG if c.name == src_l["true_category"])
            emp = next(e for e in employees if e.email == src_t["caller_email"])
            opened = datetime.fromisoformat(src_t["opened_at"]) + timedelta(minutes=rng.randint(5, 180))
            app = src_l["true_application"]
            duplicate_of = src_t["sys_id"]
            noise_applied.append("duplicate")
        else:
            app = _weighted(rng, spec.applications, spec.app_weights)

        style = _weighted(rng, STYLES, STYLE_WEIGHTS)
        if style not in spec.templates:
            style = "formal"
        short_t, body_t = rng.choice(spec.templates[style])
        short = _fill(short_t, spec, app, emp.city, rng)
        body = _fill(body_t, spec, app, emp.city, rng)

        if rng.random() < cfg.p_signature:
            body = f"{body}\n{noise.signature(emp.name, emp.title, emp.phone, emp.emp_id, emp.city, rng)}"
            pii_types += ["person_name", "phone_number", "employee_id"]
            noise_applied.append("signature")
        if rng.random() < 0.08:
            body += f"\nPlease reach me on my personal mail {emp.personal_email} as Outlook is down."
            pii_types.append("personal_email")
        if rng.random() < cfg.p_forwarded:
            mgr = rng.choice(employees)
            body = noise.forwarded_chain(body, mgr.name, mgr.email, rng)
            pii_types += ["person_name", "email_address"]
            noise_applied.append("forwarded")
        if rng.random() < cfg.p_typos:
            new_short, new_body = noise.add_typos(short, rng), noise.add_typos(body, rng)
            if (new_short, new_body) != (short, body):
                short, body = new_short, new_body
                noise_applied.append("typos")
        if rng.random() < cfg.p_lowercase:
            short, body = short.lower(), body.lower()
            noise_applied.append("lowercase")
        if rng.random() < cfg.p_html:
            new_body = noise.add_html_remnants(body, rng)
            if new_body != body:
                body = new_body
                noise_applied.append("html")
        if rng.random() < cfg.p_empty_description:
            body = ""
            pii_types = []
            noise_applied.append("empty_description")

        # What the caller selected on the portal, frequently unhelpful.
        r = rng.random()
        if r < cfg.p_blank_user_category:
            user_cat = ""
        elif r < cfg.p_blank_user_category + cfg.p_other_user_category:
            user_cat = "Other"
        elif r < cfg.p_blank_user_category + cfg.p_other_user_category + cfg.p_wrong_user_category:
            user_cat = rng.choice([c for c in USER_CATEGORIES if c != spec.user_category])
            noise_applied.append("wrong_user_category")
        else:
            user_cat = spec.user_category

        r = rng.random()
        if r < cfg.p_cmdb_ci_wrong:
            cmdb_ci = rng.choice([a for a in all_apps if a != app])
            noise_applied.append("wrong_cmdb_ci")
        elif r < cfg.p_cmdb_ci_wrong + cfg.p_cmdb_ci_set:
            cmdb_ci = app
        else:
            cmdb_ci = ""

        r = rng.random()
        if r < cfg.p_lands_on_default_group:
            group = "SD-L1"
        elif r < cfg.p_lands_on_default_group + cfg.p_wrong_group:
            group = rng.choice([g for g in all_groups if g != spec.assignment_group])
            noise_applied.append("misrouted")
        else:
            group = spec.assignment_group

        priority = _weighted(rng, list(spec.priority_weights), list(spec.priority_weights.values()))
        sys_id = _sys_id(rng)
        tickets.append({
            "sys_id": sys_id,
            "number": f"INC{10001 + len(tickets):07d}",
            "opened_at": opened.isoformat(),
            "caller_id": emp.name,
            "caller_email": emp.email,
            "u_vip": "true" if emp.vip else "false",
            "location": emp.city,
            "contact_type": _weighted(rng, [c for c, _ in CONTACT_TYPES], [w for _, w in CONTACT_TYPES]),
            "short_description": short,
            "description": body,
            "category": user_cat,
            "cmdb_ci": cmdb_ci,
            "priority": priority,
            "state": "1",
            "assignment_group": group,
        })
        labels.append({
            "sys_id": sys_id,
            "true_category": spec.name,
            "true_application": app,
            "risk_tier": spec.risk_tier,
            "true_assignment_group": spec.assignment_group,
            "style": style,
            "duplicate_of": duplicate_of,
            "pii_types": sorted(set(pii_types)),
            "noise": noise_applied,
        })

    order = sorted(range(len(tickets)), key=lambda i: tickets[i]["opened_at"])
    tickets = [tickets[i] for i in order]
    labels = [labels[i] for i in order]
    for i, t in enumerate(tickets):  # renumber chronologically, like a real system
        t["number"] = f"INC{10001 + i:07d}"
    return tickets, labels
