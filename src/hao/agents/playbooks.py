"""Playbooks: the actions an agent may take per category (policy/playbooks.yaml)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from hao.policy.schema import Level, Policy

TOOLS = {
    "identity.reset_password",
    "identity.unlock_account",
    "customer.reply",
    "incident.resolve",
    "software.request_install",
}


class Action(BaseModel):
    tool: str
    args: dict[str, str] = Field(default_factory=dict)


class Playbook(BaseModel):
    actions: list[Action]
    reply: str
    close_notes: str = ""


class Playbooks(BaseModel):
    routing_min_level: Level
    qa_sample_rate: float = Field(ge=0, le=1)
    approval_timeout_hours: float = Field(default=4, gt=0)
    reason_codes: list[str]
    software_catalog: list[str] = Field(default_factory=list)
    playbooks: dict[str, Playbook]

    @model_validator(mode="after")
    def tools_exist(self) -> Playbooks:
        bad = [f"{c}: {a.tool}" for c, pb in self.playbooks.items() for a in pb.actions if a.tool not in TOOLS]
        if bad:
            raise ValueError(f"unknown tools in playbooks: {bad}")
        return self

    def check_against(self, policy: Policy) -> list[str]:
        """Cross-check with the autonomy policy. Returns a list of problems (empty = OK)."""
        problems = [f"playbook for unknown category '{c}'" for c in self.playbooks if c not in policy.categories]
        for c, pb in self.playbooks.items():
            cat = policy.categories.get(c)
            if cat and cat.max_level < Level.A2:
                problems.append(f"playbook '{c}' can never run: category max_level is {cat.max_level}")
            if not pb.actions:
                problems.append(f"playbook '{c}' has no actions")
        return problems


def load_playbooks(policy_dir: Path) -> Playbooks:
    return Playbooks.model_validate(yaml.safe_load((policy_dir / "playbooks.yaml").read_text()))
