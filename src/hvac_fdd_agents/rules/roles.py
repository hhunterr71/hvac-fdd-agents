"""Resolve open-fdd roles to points, using a unit's Haystack tags."""

from pathlib import Path

import yaml

from hvac_fdd_agents.data.points_config import PointsConfig

# Our equipment types -> the role-map section and open-fdd equipment kind they use.
KIND = {
    "vav": "vav", "vav-parallel": "vav", "vav-series": "vav", "vav-dualDuct": "vav",
    "ahu": "ahu", "ahu-dualDuct": "ahu", "rtu": "ahu",
}


def load_role_map(path: Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def best_match(cfg: PointsConfig, unit: str, tags: list[str]) -> str | None:
    """The unique most-specific kept point on `unit` carrying all `tags`, or None."""
    want = set(tags)
    extras = {p: len(set(x.tags) - want) for p, x in cfg.points.items()
              if x.kept and x.unit == unit and want <= set(x.tags)}
    if not extras:
        return None
    best = min(extras.values())
    hits = [p for p, n in extras.items() if n == best]
    return hits[0] if len(hits) == 1 else None


def resolve(cfg: PointsConfig, unit: str, role_map: dict) -> dict[str, str]:
    """Map each open-fdd role this unit can supply to a point name."""
    kind = KIND.get(cfg.units[unit].type)
    if kind is None or kind not in role_map:
        return {}
    feeds = cfg.units[unit].feeds
    out = {}
    for role, candidates in role_map[kind].items():
        for c in candidates:
            source = feeds if c.get("from") == "served_by" else unit
            if source is None:
                continue
            point = best_match(cfg, source, c["tags"])
            if point:
                out[role] = point
                break
    return out
