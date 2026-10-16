"""The two-stage tagging graph.

    equipment_agent ──▶ equipment_checkpoint (human approves units) ──▶ tag_unit × groups (parallel)

Identical units (same type, same generic point names, same profile shapes) are tagged
once: one tagger per group, and its decisions are copied to the group's other units.

Each agent's output is checked by validate.py; on failure the agent gets one retry with
the problems listed. Whatever still fails goes to the Review Queue instead of silently
into the config.
"""

import operator
from typing import Annotated, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from hvac_fdd_agents.claude.client import StructuredLLM, Usage
from hvac_fdd_agents.tagging import prompts, siblings
from hvac_fdd_agents.tagging.schemas import EquipmentProposal, PointDecision, UnitTagging
from hvac_fdd_agents.tagging.validate import check_decisions, check_equipment


class TagState(TypedDict, total=False):
    system: str
    points: list[str]
    profiles: dict[str, str]                 # point -> profile line
    kinds: dict[str, str]                    # point -> profile shape (constant / discrete / continuous)
    equipment: dict                          # approved EquipmentProposal as a dict
    equipment_problems: list[str]
    decisions: Annotated[list[dict], operator.add]
    review: Annotated[list[dict], operator.add]
    usage: Annotated[list[dict], operator.add]


class UnitTask(TypedDict):
    unit: dict
    siblings: list[dict]                     # [{"unit": unit, "mapping": {rep_point: sibling_point}}]
    profiles: dict[str, str]


def build_graph(llm: StructuredLLM, checkpointer=None):
    def equipment_agent(state: TagState) -> dict:
        usage = Usage()
        lines = [state["profiles"][p] for p in state["points"]]
        user = prompts.equipment_user(state["system"], lines)
        proposal = llm.structured(prompts.EQUIPMENT_SYSTEM, user, EquipmentProposal, usage)
        problems = check_equipment(proposal, state["points"])
        if problems:
            proposal = llm.structured(prompts.EQUIPMENT_SYSTEM, prompts.retry_user(user, problems),
                                      EquipmentProposal, usage)
            problems = check_equipment(proposal, state["points"])
        return {"equipment": proposal.model_dump(), "equipment_problems": problems,
                "usage": [{"stage": "equipment", **usage.as_dict()}]}

    def equipment_checkpoint(state: TagState) -> dict:
        # Pauses the graph. The caller resumes with {"approved": bool, "equipment": <edited dict or None>}.
        decision = interrupt({"equipment": state["equipment"], "problems": state["equipment_problems"]})
        if not decision.get("approved"):
            raise RuntimeError("equipment proposal rejected at the checkpoint")
        equipment = decision.get("equipment") or state["equipment"]
        problems = check_equipment(EquipmentProposal.model_validate(equipment), state["points"])
        if problems:
            raise RuntimeError("approved equipment still has problems: " + "; ".join(problems))
        return {"equipment": equipment, "equipment_problems": []}

    def fan_out(state: TagState) -> list[Send]:
        kinds = state.get("kinds") or {}
        return [Send("tag_unit", {"unit": g["rep"], "siblings": g["siblings"],
                                  "profiles": {p: state["profiles"][p] for p in g["rep"]["points"]}})
                for g in siblings.groups(state["equipment"]["units"], kinds)]

    def tag_unit(task: UnitTask) -> dict:
        unit, usage = task["unit"], Usage()
        lines = [task["profiles"][p] for p in unit["points"]]
        sibling_ids = [x["unit"]["id"] for x in task["siblings"]]
        user = prompts.tagger_user(unit["id"], unit["type"], unit["served_by"], lines, sibling_ids)
        result = llm.structured(prompts.TAGGER_SYSTEM, user, UnitTagging, usage)
        problems = check_decisions(result.decisions, unit["points"], unit["id"])
        if problems:
            result = llm.structured(prompts.TAGGER_SYSTEM, prompts.retry_user(user, problems), UnitTagging, usage)
            problems = check_decisions(result.decisions, unit["points"], unit["id"])
        decisions, review = finalize(result.decisions, unit["points"], unit["id"], problems)
        copied, copied_review = apply_to_siblings(decisions, review, unit["id"], task["siblings"])
        stage = f"tag {unit['id']}" + (f" (+{', '.join(sibling_ids)})" if sibling_ids else "")
        return {"decisions": decisions + copied, "review": review + copied_review,
                "usage": [{"stage": stage, **usage.as_dict()}]}

    g = StateGraph(TagState)
    g.add_node("equipment_agent", equipment_agent)
    g.add_node("equipment_checkpoint", equipment_checkpoint)
    g.add_node("tag_unit", tag_unit)
    g.add_edge(START, "equipment_agent")
    g.add_edge("equipment_agent", "equipment_checkpoint")
    g.add_conditional_edges("equipment_checkpoint", fan_out, ["tag_unit"])
    g.add_edge("tag_unit", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())


def finalize(decisions: list[PointDecision], points: list[str], unit_id: str,
             problems: list[str]) -> tuple[list[dict], list[dict]]:
    """Attach each decision to its unit and route anything doubtful to the Review Queue."""
    by_point = {d.point: d for d in decisions if d.point in points}
    out, review = [], []
    for p in points:
        d = by_point.get(p)
        if d is None:
            review.append({"point": p, "unit": unit_id, "reason": "agent gave no decision"})
            continue
        row = {"point": p, "unit": unit_id, **d.model_dump()}
        out.append(row)
        reasons = []
        if d.confidence == "low":
            reasons.append("low confidence")
        reasons += [msg for msg in problems if msg.startswith(f"{p}:") or f" {p} " in f" {msg} "]
        if reasons:
            review.append({"point": p, "unit": unit_id, "reason": "; ".join(reasons)})
    return out, review


def apply_to_siblings(decisions: list[dict], review: list[dict], rep_id: str,
                      sibs: list[dict]) -> tuple[list[dict], list[dict]]:
    """Copy the representative unit's decisions onto each identical unit."""
    copied, copied_review = [], []
    for sib in sibs:
        uid, mapping = sib["unit"]["id"], sib["mapping"]
        copied += [{**d, "point": mapping[d["point"]], "unit": uid} for d in decisions]
        copied_review += [{**r, "point": mapping[r["point"]], "unit": uid,
                           "reason": f"{r['reason']} (same decision as {r['point']} on {rep_id})"} for r in review]
    return copied, copied_review
