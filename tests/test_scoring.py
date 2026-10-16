import copy

from hvac_fdd_agents import paths
from hvac_fdd_agents.data import points_config
from hvac_fdd_agents.data.points_config import Point, PointsConfig, Unit
from hvac_fdd_agents.scoring.tagging import score

PFPU = paths.points_config("PFPU")


def test_reference_scores_perfectly_against_itself():
    ref = points_config.load(PFPU)
    s = score(ref, ref)
    assert s.equipment["points_on_right_unit"] == 1.0
    assert s.selection["agreement"] == 1.0
    assert s.tags["exact"] == 1.0
    assert s.mismatches == []


def test_unit_ids_dont_matter():
    ref = points_config.load(PFPU)
    renamed = {"AHU-1": "AH1", "VAV-I": "box-a", "VAV-W": "box-b", "VAV-S": "box-c", "VAV-E": "box-d"}
    agent = PointsConfig(
        system="PFPU",
        units={renamed[u]: Unit(type=x.type, feeds=renamed.get(x.feeds) if x.feeds else None)
               for u, x in ref.units.items()},
        points={n: Point(unit=renamed.get(p.unit) if p.unit else None, tags=p.tags, exclude=p.exclude)
                for n, p in ref.points.items()},
    )
    s = score(agent, ref)
    assert s.unit_map["box-c"] == "VAV-S"
    assert s.equipment["points_on_right_unit"] == 1.0
    assert s.equipment["feeds_accuracy"] == 1.0


def test_wrong_tags_and_exclusions_are_counted():
    ref = points_config.load(PFPU)
    agent = copy.deepcopy(ref)
    agent.points["VAV_DMPR_S"].tags = ["damper", "cmd"]                 # sensor called a cmd
    agent.points["RM_TEMP_S"] = Point(exclude="looked redundant")        # useful point dropped
    agent.points["HWC_MWT"] = Point(unit="AHU-1", tags=["hot-water", "temp", "sensor"])  # junk kept
    s = score(agent, ref)
    assert s.selection["useful_point_excluded"] == 1
    assert s.selection["junk_point_kept"] == 1
    assert s.tags["exact"] < 1.0 and s.tags["function_correct"] < 1.0
    assert {m["point"] for m in s.mismatches} == {"VAV_DMPR_S", "RM_TEMP_S", "HWC_MWT"}


def test_extra_tags_are_rule_findable_but_missing_ones_are_not():
    ref = points_config.load(PFPU)
    agent = copy.deepcopy(ref)
    agent.points["HWC_VLV_DM"].tags = ["hot-water", "valve", "cmd", "heating"]   # extra tag: still found
    agent.points["RM_TEMP_S"].tags = ["air", "temp", "sensor"]                    # missing 'zone': not found
    s = score(agent, ref)
    findable_misses = {m["point"] for m in s.mismatches} - {"HWC_VLV_DM"}
    assert findable_misses == {"RM_TEMP_S"}
    assert s.tags["rule_findable"] == (s.tags["scored"] - 1) / s.tags["scored"]
    assert s.tags["exact"] == (s.tags["scored"] - 2) / s.tags["scored"]


def test_most_specific_match_resolves_primary_vs_discharge_airflow():
    ref = points_config.load(PFPU)
    assert ref.points["VAV_PM_CFM_S"].tags == ["air", "flow", "sensor"]
    assert score(ref, ref).tags["rule_findable"] == 1.0               # discharge airflow doesn't steal the match
    agent = copy.deepcopy(ref)
    agent.points["VAV_PM_CFM_S"].tags = ["air", "flow", "sensor", "damper"]  # now ties with discharge airflow
    s = score(agent, ref)
    assert s.tags["rule_findable"] < 1.0
