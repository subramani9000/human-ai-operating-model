from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel

from hao.config import get_settings


class CategoryDef(BaseModel):
    description: str
    default_application: str = ""


class Taxonomy(BaseModel):
    categories: dict[str, CategoryDef]
    applications: list[str]
    risk_tiers: dict[str, str]  # category -> risk tier, from autonomy.yaml

    def risk_rank(self, category: str) -> int:
        order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        return order.get(self.risk_tiers.get(category, "critical"), 3)  # unknown counts as critical


def load_taxonomy(policy_dir: Path) -> Taxonomy:
    tax = yaml.safe_load((policy_dir / "taxonomy.yaml").read_text())
    autonomy = yaml.safe_load((policy_dir / "autonomy.yaml").read_text())
    return Taxonomy(
        categories=tax["categories"],
        applications=list(autonomy["applications"]),
        risk_tiers={k: v["risk_tier"] for k, v in autonomy["categories"].items()},
    )


@lru_cache
def default_taxonomy() -> Taxonomy:
    return load_taxonomy(get_settings().policy_dir)
