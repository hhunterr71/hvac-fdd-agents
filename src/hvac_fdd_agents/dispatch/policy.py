"""The policy gate: approve / needs_human / block, from declarative rules in config/policy.yaml."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Action:
    action: str                                   # e.g. create_work_order, change_setpoint
    target_tags: list[str] = field(default_factory=list)
    within_bounds: bool | None = None


@dataclass
class Decision:
    decision: str                                 # approve / needs_human / block
    rule: str                                     # the rule that fired (logged with every decision)


def load(path: Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def evaluate(policy: dict, action: Action) -> Decision:
    for rule in policy["rules"]:
        if _matches(rule["when"], action):
            return Decision(rule["decision"], rule["name"])
    return Decision(policy.get("default", "block"), "default")


def _matches(when: dict, a: Action) -> bool:
    for key, want in when.items():
        if key == "action":
            if a.action not in (want if isinstance(want, list) else [want]):
                return False
        elif key == "target_tags_any":
            if not set(want) & set(a.target_tags):
                return False
        elif key == "within_bounds":
            if a.within_bounds is not want:
                return False
        else:
            return False                          # unknown condition: never match by accident
    return True
