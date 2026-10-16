import uuid

from langgraph.types import Command

from hvac_fdd_agents import paths
from hvac_fdd_agents.data import points_config
from hvac_fdd_agents.tagging.graph import build_graph
from hvac_fdd_agents.tagging.siblings import generic_names, groups
from tests.conftest import FakeLLM


def units_from(system: str) -> list[dict]:
    cfg = points_config.load(paths.points_config(system))
    return [{"id": uid, "type": u.type, "served_by": u.feeds,
             "points": [p for p, x in cfg.points.items() if x.unit == uid]} for uid, u in cfg.units.items()]


def group_ids(gs) -> set[tuple]:
    return {tuple([g["rep"]["id"], *[s["unit"]["id"] for s in g["siblings"]]]) for g in gs}


def test_generic_names_strip_the_unit_token():
    assert generic_names(["VAV_DMPR_S", "RM_TEMP_S"]) == {"VAV_DMPR_{u}": "VAV_DMPR_S", "RM_TEMP_{u}": "RM_TEMP_S"}
    assert generic_names(["SA_TEMP", "OA_TEMP"]) is None          # an AHU's points share no token


def test_lbnl_vav_boxes_form_one_group():
    gs = groups(units_from("PFPU"), kinds={})
    assert group_ids(gs) == {("AHU-1",), ("VAV-E", "VAV-I", "VAV-S", "VAV-W")}
    vav = next(g for g in gs if g["siblings"])
    assert vav["siblings"][0]["mapping"]["VAV_DMPR_E"] in {"VAV_DMPR_I", "VAV_DMPR_S", "VAV_DMPR_W"}


def test_chiller_plant_groups_repeated_equipment():
    ids = group_ids(groups(units_from("CHILLER"), kinds={}))
    assert ("CH-1", "CH-2", "CH-3") in ids
    assert ("CT-1", "CT-2", "CT-3") in ids
    assert ("CWP-1", "CWP-2", "CWP-3") in ids
    assert ("CHW-LOOP",) in ids and ("CW-LOOP",) in ids


def test_a_box_that_behaves_differently_is_tagged_on_its_own():
    units = units_from("PFPU")
    kinds = {p: "continuous" for u in units for p in u["points"]}
    kinds["VAV_DMPR_S"] = "constant"                                 # e.g. a stuck damper
    ids = group_ids(groups(units, kinds))
    assert ("VAV-S",) in ids and ("VAV-E", "VAV-I", "VAV-W") in ids


def test_graph_tags_identical_units_once_and_copies_decisions():
    units = [{"id": f"VAV-{z}", "type": "vav", "served_by": None, "points": [f"DMPR_{z}", f"FLOW_{z}"],
              "rationale": ""} for z in ("A", "B", "C")]
    points = [p for u in units for p in u["points"]]
    tagging = {"decisions": [
        {"point": "DMPR_A", "action": "tag", "tags": ["damper", "sensor"], "exclude_reason": "", "confidence": "low"},
        {"point": "FLOW_A", "action": "tag", "tags": ["discharge", "air", "flow", "sensor"], "exclude_reason": "",
         "confidence": "high"}]}
    llm = FakeLLM({"EquipmentProposal": [{"units": units, "notes": ""}], "UnitTagging": [tagging]})
    graph = build_graph(llm)
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    graph.invoke({"system": "x", "points": points, "profiles": {p: p for p in points},
                  "kinds": {p: "continuous" for p in points}}, config)
    state = graph.invoke(Command(resume={"approved": True}), config)

    assert [c[0] for c in llm.calls].count("UnitTagging") == 1
    assert "identical unit(s): VAV-B, VAV-C" in llm.calls[-1][1]
    by_point = {d["point"]: d for d in state["decisions"]}
    assert by_point["FLOW_C"]["unit"] == "VAV-C" and by_point["FLOW_C"]["tags"] == ["discharge", "air", "flow", "sensor"]
    assert {r["point"] for r in state["review"]} == {"DMPR_A", "DMPR_B", "DMPR_C"}
