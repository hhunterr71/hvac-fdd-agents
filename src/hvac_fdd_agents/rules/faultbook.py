"""The Fault Book: which open-fdd rules run on which equipment, with what thresholds.

Format (config/faultbook.yaml):

    version: 1
    source: <where the thresholds came from>
    equipment:
      vav:
        VAV-4: {}                          # enabled, open-fdd defaults
        VAV-7: {fixed_flow_min_mean: 250}  # enabled, one threshold overridden
        VAV-1: {enabled: false}            # off
"""

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class FaultBook:
    version: int
    source: str
    rules: dict[str, dict[str, dict]]     # kind -> rule id -> param overrides

    def enabled(self, kind: str) -> dict[str, dict]:
        return {rid: {k: v for k, v in p.items() if k != "enabled"}
                for rid, p in self.rules.get(kind, {}).items() if p.get("enabled", True)}


def load(path: Path) -> FaultBook:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    rules = {kind: {rid: (p or {}) for rid, p in (rs or {}).items()} for kind, rs in raw["equipment"].items()}
    return FaultBook(version=raw["version"], source=raw.get("source", ""), rules=rules)


def seed(kinds: list[str], path: Path) -> None:
    """Write v1: every open-fdd cookbook rule that applies to each kind, at its defaults."""
    import open_fdd
    from open_fdd.rules import CANONICAL_RULES

    lines = [
        "# The Fault Book. v1 = every applicable open-fdd cookbook rule at its default thresholds",
        "# (open-fdd implements the ASHRAE Guideline 36 AHU fault conditions plus VAV, plant, and",
        "# sensor rules). Overrides go inside {}: e.g. VAV-7: {fixed_flow_min_mean: 250}.",
        "# Turn a rule off with {enabled: false}. Changes are proposed by the Fault Book agent",
        "# and approved in the Review Queue.",
        "version: 1",
        f'source: "open-fdd {getattr(open_fdd, "__version__", "?")} cookbook defaults"',
        "equipment:",
    ]
    for kind in kinds:
        lines.append(f"  {kind}:")
        for r in CANONICAL_RULES:
            if kind in r.equipment_kinds:
                lines.append(f"    {r.id}: {{}}  # {r.title}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
