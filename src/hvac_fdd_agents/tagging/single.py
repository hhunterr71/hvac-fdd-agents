"""Single-agent baseline: one call that groups and tags every point at once.

Same inputs, same validator, same output format as the graph, so the two can be
scored side by side.
"""

from hvac_fdd_agents.claude.client import StructuredLLM, Usage
from hvac_fdd_agents.tagging import prompts
from hvac_fdd_agents.tagging.graph import finalize
from hvac_fdd_agents.tagging.schemas import EquipmentProposal, SingleAgentOutput
from hvac_fdd_agents.tagging.validate import check_decisions, check_equipment


def run_single(llm: StructuredLLM, system: str, points: list[str], profiles: dict[str, str]) -> dict:
    usage = Usage()
    user = prompts.equipment_user(system, [profiles[p] for p in points])
    out = llm.structured(prompts.SINGLE_AGENT_SYSTEM, user, SingleAgentOutput, usage)
    problems = _problems(out, points)
    if problems:
        out = llm.structured(prompts.SINGLE_AGENT_SYSTEM, prompts.retry_user(user, problems), SingleAgentOutput, usage)
        problems = _problems(out, points)

    equipment = EquipmentProposal(units=out.units, notes=out.notes)
    decisions, review = [], []
    for unit in out.units:
        unit_points = [p for p in unit.points if p in points]
        mine = [d for d in out.decisions if d.point in unit_points]
        unit_problems = [m for m in problems if any(m.startswith(f"{p}:") for p in unit_points)]
        d, r = finalize(mine, unit_points, unit.id, unit_problems)
        decisions += d
        review += r
    return {"equipment": equipment.model_dump(), "equipment_problems": check_equipment(equipment, points),
            "decisions": decisions, "review": review, "usage": [{"stage": "single", **usage.as_dict()}]}


def _problems(out: SingleAgentOutput, points: list[str]) -> list[str]:
    problems = check_equipment(EquipmentProposal(units=out.units, notes=out.notes), points)
    for unit in out.units:
        unit_points = [p for p in unit.points if p in points]
        problems += check_decisions([d for d in out.decisions if d.point in unit_points], unit_points, unit.id)
    return problems
