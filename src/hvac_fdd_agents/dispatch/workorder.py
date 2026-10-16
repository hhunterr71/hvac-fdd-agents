"""Build one Work Order per dispatched Incident (ADR-0006). Priority is computed, never chosen by a model."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from hvac_fdd_agents.dispatch.diagnose import NONE, Diagnosis
from hvac_fdd_agents.dispatch.incidents import DispatchConfig, Incident
from hvac_fdd_agents.dispatch.policy import Action, evaluate


@dataclass
class WorkOrder:
    unit: str
    as_of: str
    priority: int
    fault: str
    trade: str
    action: str
    consequence: str
    persistence: str
    hours_per_day: float
    evidence: str
    also_observed: list[str] = field(default_factory=list)
    gate: str = ""
    gate_rule: str = ""
    method: str = ""

    def render(self) -> str:
        lines = [f"WORK ORDER  P{self.priority}  {self.unit}  (as of {self.as_of})",
                 f"Likely problem: {self.fault.replace('_', ' ')}  [{self.persistence}, ~{self.hours_per_day} h/day]",
                 f"Send: {self.trade}",
                 f"Do: {self.action}",
                 f"Why we think so: {self.evidence}"]
        if self.also_observed:
            lines.append("Also observed: " + "; ".join(self.also_observed))
        return "\n".join(lines)


def load_remediation(path: Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def priority(consequence: str, persistence: str, hours_per_day: float, remediation: dict) -> int:
    p = remediation["priority_base"][consequence]
    if persistence == "weekly":
        p += 1
    if hours_per_day >= 8:
        p -= 1
    return max(1, min(4, p))


def build(incident: Incident, diagnosis: Diagnosis, cfg: DispatchConfig, remediation: dict,
          policy: dict) -> WorkOrder | None:
    """A Work Order if the Incident is persistent and the diagnosis isn't 'none'; else None (Watch List)."""
    if not incident.dispatchable(cfg) or diagnosis.fault == NONE:
        return None
    fix = remediation["faults"].get(diagnosis.fault)
    if fix is None:
        return None
    persistent = incident.persistent(cfg)
    pattern = incident.pattern(cfg)
    hours = incident.magnitude(cfg)
    evidence = diagnosis.explanation or (
        "Rules firing persistently on this unit beyond its identical siblings: "
        + ", ".join(f"{f.rule} ({f.pattern}, {f.hours_per_day} h/day)" for f in persistent)
        + f". Signature match: {diagnosis.fault.replace('_', ' ')} ({diagnosis.probability:.0%}).")
    others = [f"{f.rule} ({f.pattern})" for f in incident.findings if f not in persistent and f.pattern != "stale"]
    runner_ups = [f"possible {f.replace('_', ' ')} ({p:.0%})" for f, p in diagnosis.ranking[1:3] if f != NONE and p >= 0.1]
    gate = evaluate(policy, Action("create_work_order"))
    return WorkOrder(
        unit=incident.unit, as_of=str(incident.as_of.date()),
        priority=priority(fix["consequence"], pattern, hours, remediation),
        fault=diagnosis.fault, trade=fix["trade"], action=fix["action"], consequence=fix["consequence"],
        persistence=pattern, hours_per_day=hours, evidence=evidence, also_observed=runner_ups + others,
        gate=gate.decision, gate_rule=gate.rule, method=diagnosis.method,
    )
