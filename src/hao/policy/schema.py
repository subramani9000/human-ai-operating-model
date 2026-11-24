"""Policy schema: loads and validates policy/autonomy.yaml + policy/ownership.yaml.

Validation is how we keep the operating model honest: a policy that would let AI act
on payroll, or a category with no accountable owner, fails CI (ADR 0001).
"""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator


class Level(StrEnum):
    A0 = "A0"  # human only
    A1 = "A1"  # AI assists, human leads
    A2 = "A2"  # AI drafts, human approves
    A3 = "A3"  # AI acts

    @property
    def rank(self) -> int:
        return int(self.value[1])

    def __le__(self, other: Level) -> bool:  # type: ignore[override]
        return self.rank <= other.rank

    def __lt__(self, other: Level) -> bool:  # type: ignore[override]
        return self.rank < other.rank


class Owner(BaseModel):
    name: str
    email: str
    role: str


class RiskTier(BaseModel):
    max_level: Level


class Application(BaseModel):
    risk_tier: str
    max_level: Level


class Category(BaseModel):
    owner: str
    risk_tier: str
    current_level: Level
    max_level: Level
    assignment_group: str


class RuleCondition(BaseModel):
    confidence_below: float | None = Field(default=None, ge=0, le=1)
    priority_in: list[str] | None = None
    requester_is_vip: bool | None = None
    reopen_count_at_least: int | None = Field(default=None, ge=0)
    text_matches: str | None = None  # case-insensitive regex over the (redacted) ticket text

    @model_validator(mode="after")
    def at_least_one(self) -> RuleCondition:
        if self.text_matches is not None:
            try:
                re.compile(self.text_matches)
            except re.error as exc:
                raise ValueError(f"text_matches is not a valid regex: {exc}") from exc
        if not any(v is not None for v in self.model_dump().values()):
            raise ValueError("rule condition must set at least one field")
        return self


class RuleEffect(BaseModel):
    downgrade: int | None = Field(default=None, ge=1, le=3)
    cap: Level | None = None

    @model_validator(mode="after")
    def exactly_one(self) -> RuleEffect:
        if (self.downgrade is None) == (self.cap is None):
            raise ValueError("rule effect must set exactly one of 'downgrade' or 'cap'")
        return self


class Rule(BaseModel):
    id: str
    when: RuleCondition
    effect: RuleEffect


class Controller(BaseModel):
    window_days: int = Field(gt=0)
    min_samples: int = Field(gt=0)
    promote_precision: float = Field(gt=0, le=1)
    demote_precision: float = Field(gt=0, le=1)
    demote_min_samples: int = Field(default=10, gt=0)  # demotion acts on less evidence than promotion
    minor_reasons: list[str] = Field(default_factory=lambda: ["tone"])  # edits for these still count as correct
    max_reopen_rate: float = Field(default=0.10, gt=0, le=1)  # A3 demotes above this share of reopened executions
    reopen_min_executions: int = Field(default=20, gt=0)
    reopen_window_hours: int = Field(gt=0)

    @model_validator(mode="after")
    def thresholds_ordered(self) -> Controller:
        if self.demote_precision >= self.promote_precision:
            raise ValueError("demote_precision must be lower than promote_precision (avoids flapping)")
        return self


class UnknownCategory(BaseModel):
    owner: str
    level: Level
    assignment_group: str


class Policy(BaseModel):
    version_label: str
    risk_tiers: dict[str, RiskTier]
    applications: dict[str, Application]
    categories: dict[str, Category]
    unknown_category: UnknownCategory
    rules: list[Rule]
    controller: Controller
    owners: dict[str, Owner]
    version: str = ""  # content hash, filled by load_policy

    @model_validator(mode="after")
    def cross_checks(self) -> Policy:
        errors: list[str] = []
        for name, app in self.applications.items():
            tier = self.risk_tiers.get(app.risk_tier)
            if tier is None:
                errors.append(f"application '{name}': unknown risk_tier '{app.risk_tier}'")
            elif tier.max_level < app.max_level:
                errors.append(f"application '{name}': max_level {app.max_level} exceeds tier cap {tier.max_level}")
        for name, cat in self.categories.items():
            if cat.owner not in self.owners:
                errors.append(f"category '{name}': owner '{cat.owner}' not defined in ownership.yaml")
            tier = self.risk_tiers.get(cat.risk_tier)
            if tier is None:
                errors.append(f"category '{name}': unknown risk_tier '{cat.risk_tier}'")
            elif tier.max_level < cat.max_level:
                errors.append(f"category '{name}': max_level {cat.max_level} exceeds tier cap {tier.max_level}")
            if cat.max_level < cat.current_level:
                errors.append(f"category '{name}': current_level {cat.current_level} above max_level {cat.max_level}")
        if self.unknown_category.owner not in self.owners:
            errors.append(f"unknown_category: owner '{self.unknown_category.owner}' not defined in ownership.yaml")
        if self.unknown_category.level != Level.A0:
            errors.append("unknown_category: level must be A0 (unclassifiable work goes to a human)")
        ids = [r.id for r in self.rules]
        if len(ids) != len(set(ids)):
            errors.append("rule ids must be unique")
        if errors:
            raise ValueError("; ".join(errors))
        return self


def load_policy(policy_dir: Path) -> Policy:
    autonomy_raw = (policy_dir / "autonomy.yaml").read_bytes()
    ownership_raw = (policy_dir / "ownership.yaml").read_bytes()
    data = yaml.safe_load(autonomy_raw)
    data["owners"] = yaml.safe_load(ownership_raw)["owners"]
    data["version"] = hashlib.sha256(autonomy_raw + b"\0" + ownership_raw).hexdigest()[:12]
    return Policy.model_validate(data)
