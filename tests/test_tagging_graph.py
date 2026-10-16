import uuid

import pytest
import yaml
from langgraph.types import Command

from hvac_fdd_agents.data import points_config
from hvac_fdd_agents.scoring.tagging import score
from hvac_fdd_agents.tagging.graph import build_graph
from hvac_fdd_agents.tagging.run import tag_trend_file
from tests.conftest import FakeLLM

POINTS = ["SAT", "SAT_SP", "FAN_STS", "DMPR_1", "FLOW_1", "ZN_T_1", "CONST_X"]

EQUIPMENT = {"units": [
    {"id": "AHU-1", "type": "ahu", "served_by": None, "points": ["SAT", "SAT_SP", "FAN_STS", "CONST_X"],
     "rationale": "supply air and fan"},
    {"id": "VAV-1", "type": "vav", "served_by": "AHU-1", "points": ["DMPR_1", "FLOW_1", "ZN_T_1"],
     "rationale": "_1 suffix"},
], "notes": ""}


def d(point, tags=None, exclude="", confidence="high"):
    return {"point": point, "action": "exclude" if exclude else "tag", "tags": tags or [],
            "exclude_reason": exclude, "confidence": confidence}


def tagger(user):
    if user.startswith("Unit AHU-1"):
        return {"decisions": [d("SAT", ["discharge", "air", "temp", "sensor"]),
                              d("SAT_SP", ["discharge", "air", "temp", "sp"]),
                              d("FAN_STS", ["discharge", "fan", "run", "sensor"]),
                              d("CONST_X", exclude="constant configuration value")]}
    return {"decisions": [d("DMPR_1", ["damper", "sensor"], confidence="low"),
                          d("FLOW_1", ["discharge", "air", "flow", "sensor"]),
                          d("ZN_T_1", ["zone", "air", "temp", "sensor"])]}


def test_graph_pauses_at_equipment_checkpoint_then_tags_units_in_parallel():
    llm = FakeLLM({"EquipmentProposal": [EQUIPMENT], "UnitTagging": [tagger]})
    graph = build_graph(llm)
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    profiles = {p: f"{p}: profile" for p in POINTS}

    state = graph.invoke({"system": "tiny", "points": POINTS, "profiles": profiles}, config)
    assert "__interrupt__" in state                                  # paused for a human
    assert [c[0] for c in llm.calls] == ["EquipmentProposal"]        # no tagging before approval

    state = graph.invoke(Command(resume={"approved": True}), config)
    assert sorted(c[0] for c in llm.calls) == ["EquipmentProposal", "UnitTagging", "UnitTagging"]
    assert {x["point"] for x in state["decisions"]} == set(POINTS)
    assert [r["point"] for r in state["review"]] == ["DMPR_1"]       # low confidence -> Review Queue


def test_rejecting_at_the_checkpoint_stops_the_run():
    graph = build_graph(FakeLLM({"EquipmentProposal": [EQUIPMENT], "UnitTagging": [tagger]}))
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    graph.invoke({"system": "tiny", "points": POINTS, "profiles": {p: p for p in POINTS}}, config)
    with pytest.raises(RuntimeError, match="rejected"):
        graph.invoke(Command(resume={"approved": False}), config)


def test_invalid_output_gets_one_retry_with_the_problems_listed():
    bad = {"units": [{**EQUIPMENT["units"][0], "points": ["SAT"]}, EQUIPMENT["units"][1]], "notes": ""}
    llm = FakeLLM({"EquipmentProposal": [bad, EQUIPMENT], "UnitTagging": [tagger]})
    graph = build_graph(llm)
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    state = graph.invoke({"system": "tiny", "points": POINTS, "profiles": {p: p for p in POINTS}}, config)
    assert len(llm.calls) == 2
    assert "points not assigned to any unit" in llm.calls[1][1]
    assert state["__interrupt__"][0].value["problems"] == []


def test_end_to_end_on_a_trend_file(tiny_trend, tmp_path):
    llm = FakeLLM({"EquipmentProposal": [EQUIPMENT], "UnitTagging": [tagger]})
    out = tmp_path / "run"
    points_file = tag_trend_file(tiny_trend, "tiny", llm, out)

    cfg = points_config.load(points_file)
    assert cfg.units["VAV-1"].feeds == "AHU-1"
    assert cfg.points["CONST_X"].exclude == "constant configuration value"
    assert cfg.points["DMPR_1"].review == "low confidence"
    assert yaml.safe_load((out / "review_queue.yaml").read_text())[0]["point"] == "DMPR_1"
    assert (out / "profiles.txt").read_text().startswith("SAT:")
    assert score(cfg, cfg).tags["exact"] == 1.0


def test_agents_never_see_point_descriptions(tiny_trend, tmp_path):
    """Information isolation: prompts are built from names and profiles only."""
    llm = FakeLLM({"EquipmentProposal": [EQUIPMENT], "UnitTagging": [tagger]})
    tag_trend_file(tiny_trend, "tiny", llm, tmp_path / "run")
    for _, user in llm.calls:
        assert "desc:" not in user and "units:" not in user
