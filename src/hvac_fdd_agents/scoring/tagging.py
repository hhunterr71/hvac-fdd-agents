"""Score an agent's points config against the Reference Mapping.

Agent unit IDs are arbitrary, so each agent unit is first matched to the reference unit
that shares the most of its points. Scores are reported per stage, so a bad grouping
doesn't hide good tagging (or the reverse):

  equipment   points on the right unit, unit types, feeds links
  selection   which points were kept vs excluded
  tags        for points both sides kept: rule-findable, exact tag sets, partial overlap, function tag

"Rule-findable" is the score that matters downstream. Haystack queries match by subset,
so a fault rule asking for the reference tags (e.g. `hot-water valve cmd`) finds every
point whose tags include them, then takes the most specific match (fewest extra tags).
The point is findable if it is that unique best match. Extra descriptive tags are
harmless; missing or wrong ones are not. Example: on a fan-powered VAV, `air flow sensor`
matches both primary airflow and `discharge air flow sensor`; primary wins with no extras.
"""

from collections import Counter
from dataclasses import dataclass, field

from hvac_fdd_agents.data.haystack import FUNCTIONS
from hvac_fdd_agents.data.points_config import PointsConfig


@dataclass
class TaggingScore:
    unit_map: dict[str, str]
    equipment: dict
    selection: dict
    tags: dict
    mismatches: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        e, s, t = self.equipment, self.selection, self.tags
        return "\n".join([
            f"Equipment: {e['points_on_right_unit']:.0%} of points on the right unit; "
            f"{e['units_agent']} units proposed vs {e['units_reference']} in reference; "
            f"types {e['type_accuracy']:.0%}; feeds links {e['feeds_accuracy']:.0%}",
            f"Selection: {s['agreement']:.0%} agree on keep/exclude; "
            f"{s['useful_point_excluded']} useful point(s) wrongly excluded; "
            f"{s['junk_point_kept']} point(s) kept that reference excludes",
            f"Tags ({t['scored']} points): rule-findable {t['rule_findable']:.0%}; exact {t['exact']:.0%}; "
            f"mean overlap {t['mean_jaccard']:.0%}; function tag (sensor/sp/cmd) {t['function_correct']:.0%}",
        ])


def score(agent: PointsConfig, ref: PointsConfig) -> TaggingScore:
    shared = [p for p in ref.points if p in agent.points]
    unit_map = _match_units(agent, ref, shared)

    ref_kept = [p for p in shared if ref.points[p].kept]
    on_right_unit = sum(1 for p in ref_kept
                        if agent.points[p].unit and unit_map.get(agent.points[p].unit) == ref.points[p].unit)
    matched = {a: r for a, r in unit_map.items() if a in agent.units and r in ref.units}
    types_ok = sum(1 for a, r in matched.items() if agent.units[a].type == ref.units[r].type)
    ref_feeds = {r: u.feeds for r, u in ref.units.items() if u.feeds}
    feeds_ok = sum(1 for a, r in matched.items()
                   if r in ref_feeds and unit_map.get(agent.units[a].feeds or "") == ref_feeds[r])
    feeds_total = sum(1 for r in matched.values() if r in ref_feeds)
    equipment = {
        "points_on_right_unit": _ratio(on_right_unit, len(ref_kept)),
        "units_agent": len(agent.units), "units_reference": len(ref.units),
        "type_accuracy": _ratio(types_ok, len(matched)),
        "feeds_accuracy": _ratio(feeds_ok, feeds_total),
    }

    both_kept = [p for p in shared if ref.points[p].kept and agent.points[p].kept]
    wrongly_excluded = [p for p in shared if ref.points[p].kept and not agent.points[p].kept]
    wrongly_kept = [p for p in shared if not ref.points[p].kept and agent.points[p].kept]
    selection = {
        "agreement": _ratio(len(shared) - len(wrongly_excluded) - len(wrongly_kept), len(shared)),
        "useful_point_excluded": len(wrongly_excluded),
        "junk_point_kept": len(wrongly_kept),
    }

    exact = jaccard = function_ok = findable = 0
    mismatches = []
    for p in both_kept:
        a, r = set(agent.points[p].tags), set(ref.points[p].tags)
        exact += a == r
        findable += _rule_findable(agent, p, r)
        jaccard += len(a & r) / len(a | r) if a | r else 1
        function_ok += (a & FUNCTIONS) == (r & FUNCTIONS)
        if a != r:
            mismatches.append({"point": p, "agent": sorted(a), "reference": sorted(r),
                               "missing": sorted(r - a), "extra": sorted(a - r)})
    for p in wrongly_excluded:
        mismatches.append({"point": p, "agent": f"excluded: {agent.points[p].exclude}",
                           "reference": sorted(ref.points[p].tags)})
    for p in wrongly_kept:
        mismatches.append({"point": p, "agent": sorted(agent.points[p].tags),
                           "reference": f"excluded: {ref.points[p].exclude}"})
    tags = {"scored": len(both_kept), "rule_findable": _ratio(findable, len(both_kept)),
            "exact": _ratio(exact, len(both_kept)),
            "mean_jaccard": _ratio(jaccard, len(both_kept)),
            "function_correct": _ratio(function_ok, len(both_kept))}
    return TaggingScore(unit_map, equipment, selection, tags, mismatches)


def _rule_findable(agent: PointsConfig, point: str, ref_tags: set[str]) -> bool:
    """Is this point the unique most-specific match for the reference tags on its unit?"""
    unit = agent.points[point].unit
    extras = {p: len(set(x.tags) - ref_tags) for p, x in agent.points.items()
              if x.kept and x.unit == unit and ref_tags <= set(x.tags)}
    if point not in extras:
        return False
    return [p for p, n in extras.items() if n == extras[point]] == [point] and extras[point] == min(extras.values())


def _match_units(agent: PointsConfig, ref: PointsConfig, shared: list[str]) -> dict[str, str]:
    """Map each agent unit to the reference unit it shares the most kept points with."""
    overlap: dict[str, Counter] = {}
    for p in shared:
        a, r = agent.points[p].unit, ref.points[p].unit
        if a and r:
            overlap.setdefault(a, Counter())[r] += 1
    return {a: c.most_common(1)[0][0] for a, c in overlap.items()}


def _ratio(n: float, d: int) -> float:
    return n / d if d else 1.0
