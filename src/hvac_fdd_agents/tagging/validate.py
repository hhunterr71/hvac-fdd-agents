"""Deterministic checks on agent output. Agents propose; this code decides what's legal."""

from hvac_fdd_agents.data.haystack import EQUIPMENT_TYPES, validate_tags
from hvac_fdd_agents.tagging.schemas import EquipmentProposal, PointDecision


def check_equipment(proposal: EquipmentProposal, points: list[str]) -> list[str]:
    problems = []
    ids = [u.id for u in proposal.units]
    if len(ids) != len(set(ids)):
        problems.append(f"duplicate unit ids: {sorted({i for i in ids if ids.count(i) > 1})}")
    seen: dict[str, str] = {}
    for u in proposal.units:
        if u.type not in EQUIPMENT_TYPES:
            problems.append(f"{u.id}: type {u.type!r} is not an allowed equipment type")
        if u.served_by is not None and u.served_by not in ids:
            problems.append(f"{u.id}: served_by {u.served_by!r} is not a unit id")
        if u.served_by == u.id:
            problems.append(f"{u.id}: a unit can't serve itself")
        for p in u.points:
            if p not in points:
                problems.append(f"{u.id}: {p!r} is not a point in this trend file")
            elif p in seen:
                problems.append(f"{p!r} is assigned to both {seen[p]} and {u.id}")
            else:
                seen[p] = u.id
    missing = [p for p in points if p not in seen]
    if missing:
        problems.append(f"points not assigned to any unit: {missing}")
    return problems


def check_decisions(decisions: list[PointDecision], points: list[str], unit_id: str) -> list[str]:
    problems = []
    by_point: dict[str, PointDecision] = {}
    for d in decisions:
        if d.point not in points:
            problems.append(f"{d.point!r} is not one of {unit_id}'s points")
        elif d.point in by_point:
            problems.append(f"{d.point!r} decided twice")
        else:
            by_point[d.point] = d
        if d.action == "tag":
            problems += [f"{d.point}: {msg}" for msg in validate_tags(d.tags)]
        elif not d.exclude_reason.strip():
            problems.append(f"{d.point}: excluded without a reason")
    missing = [p for p in points if p not in by_point]
    if missing:
        problems.append(f"no decision for: {missing}")
    tag_sets: dict[frozenset, str] = {}
    for d in by_point.values():
        if d.action == "tag":
            key = frozenset(d.tags)
            if key in tag_sets:
                problems.append(f"{d.point} and {tag_sets[key]} have identical tags {sorted(key)}; "
                                f"add a tag that tells them apart, or exclude the redundant one")
            tag_sets[key] = d.point
    return problems
