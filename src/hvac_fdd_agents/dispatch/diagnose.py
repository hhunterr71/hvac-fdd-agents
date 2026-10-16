"""Diagnose an Incident: rank likely faults, and ask the model only when it's close.

1. SignatureModel (deterministic, learned): a naive Bayes over which rules persist on a
   unit. For each fault type it learns how often each rule persists when that fault is
   present, from labeled training Incidents. It also learns a "none" class from
   Incidents on healthy units, so it can say "this is noise" and suppress a dispatch.
2. Evidence pack (deterministic): the unit's key measurements over the lookback,
   next to the median of its identical siblings.
3. Tiebreak (model): only when the top two faults are within `margin`, the model reads
   the evidence pack and picks among the top candidates, citing numbers.
"""

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from hvac_fdd_agents.claude.client import StructuredLLM, Usage
from hvac_fdd_agents.data.points_config import PointsConfig
from hvac_fdd_agents.data.trend import quote
from hvac_fdd_agents.rules.roles import best_match

NONE = "none"
MARGIN = 0.2          # ask the model only when the top two faults are within 20 points (ADR-0007)


@dataclass
class SignatureModel:
    rule_given_fault: dict[str, dict[str, float]] = field(default_factory=dict)
    prior: dict[str, float] = field(default_factory=dict)
    rules: list[str] = field(default_factory=list)

    @classmethod
    def fit(cls, examples: list[tuple[frozenset[str], str]]) -> "SignatureModel":
        """examples: (signature = set of persistent rules, label = fault type or 'none')."""
        rules = sorted({r for sig, _ in examples for r in sig})
        counts: dict[str, Counter] = defaultdict(Counter)
        totals: Counter = Counter()
        for sig, label in examples:
            totals[label] += 1
            counts[label].update(sig)
        n = sum(totals.values())
        return cls(
            rule_given_fault={f: {r: (counts[f][r] + 1) / (totals[f] + 2) for r in rules} for f in totals},
            prior={f: totals[f] / n for f in totals},
            rules=rules,
        )

    def save(self, path: Path, header: list[str]) -> None:
        body = {"prior": {f: round(p, 6) for f, p in self.prior.items()},
                "rule_given_fault": {f: {r: round(p, 6) for r, p in ps.items()}
                                     for f, ps in self.rule_given_fault.items()}}
        text = "".join(f"# {h}\n" for h in header) + yaml.safe_dump(body, sort_keys=True)
        Path(path).write_text(text, encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "SignatureModel":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        rules = sorted({r for ps in raw["rule_given_fault"].values() for r in ps})
        return cls(rule_given_fault=raw["rule_given_fault"], prior=raw["prior"], rules=rules)

    def rank(self, signature: frozenset[str]) -> list[tuple[str, float]]:
        """Faults with probabilities, most likely first."""
        scores = {}
        for f, p in self.rule_given_fault.items():
            s = math.log(self.prior[f])
            for r in self.rules:
                s += math.log(p[r]) if r in signature else math.log(1 - p[r])
            scores[f] = s
        top = max(scores.values())
        z = sum(math.exp(s - top) for s in scores.values())
        return sorted(((f, math.exp(s - top) / z) for f, s in scores.items()), key=lambda x: -x[1])


# Evidence: (label, tags on the unit or its feeding unit). Means are over operating minutes.
EVIDENCE = [
    ("zone temp (°F)", ["zone", "air", "temp", "sensor"], False),
    ("zone cooling setpoint (°F)", ["zone", "air", "temp", "cooling", "sp"], False),
    ("zone heating setpoint (°F)", ["zone", "air", "temp", "heating", "sp"], False),
    ("box airflow (cfm)", ["air", "flow", "sensor"], False),
    ("box airflow setpoint (cfm)", ["air", "flow", "sp"], False),
    ("damper position (0-1)", ["damper", "sensor"], False),
    ("reheat valve command (0-1)", ["reheat", "valve", "cmd"], False),
    ("reheat valve position (0-1)", ["reheat", "valve", "sensor"], False),
    ("box discharge air temp (°F)", ["discharge", "air", "temp", "sensor"], False),
    ("AHU supply air temp (°F)", ["discharge", "air", "temp", "sensor"], True),
]


def evidence_pack(con, cfg: PointsConfig, unit: str, peers: list[str], start, end,
                  operating_col: str | None) -> list[dict]:
    """Mean of each key point for the unit vs. its siblings' median, over the window."""
    where = f"ts >= '{start}' and ts < '{end}'" + (f" and {quote(operating_col)} > 0.5" if operating_col else "")
    rows = []
    for label, tags, from_feed in EVIDENCE:
        src = cfg.units[unit].feeds if from_feed else unit
        point = best_match(cfg, src, tags) if src else None
        if not point:
            continue
        sib_points = [] if from_feed else [best_match(cfg, p, tags) for p in peers]
        cols = [point] + [p for p in sib_points if p]
        vals = con.execute(f"select {', '.join(f'avg({quote(c)})' for c in cols)} from trend where {where}").fetchone()
        unit_val = vals[0]
        sib_vals = sorted(v for v in vals[1:] if v is not None)
        rows.append({"measure": label, "unit": _r(unit_val),
                     "siblings_median": _r(sib_vals[len(sib_vals) // 2]) if sib_vals else None})
    return rows


def _r(x):
    return None if x is None else round(float(x), 2)


class TiebreakAnswer(BaseModel):
    fault: str = Field(description="One of the candidate fault types, or 'none' if the evidence shows no real fault")
    confidence: Literal["high", "medium", "low"]
    explanation: str = Field(description="2-4 sentences for the technician, citing the numbers that decided it")


TIEBREAK_SYSTEM = """You are an HVAC controls engineer deciding what is wrong with one terminal unit before a technician is sent.
You get: the unit, the fault-detection rules that have been firing persistently on it (and how often), a statistical ranking of candidate faults, and an evidence table comparing this unit's measurements with its identical sibling units over the same period.
Pick the single most likely fault from the candidates (or 'none' if the evidence says the unit is behaving like its siblings). Reason from the physics: e.g. a stuck damper shows damper position flat while airflow and zone temp drift; an airflow sensor bias shows airflow reading off while damper and zone temp respond normally; a leaking reheat valve shows discharge air warmer than supply air with the valve commanded closed.
Write the explanation for the technician: plain language, cite the numbers that decided it."""


def tiebreak(llm: StructuredLLM, unit: str, findings: list, ranking: list[tuple[str, float]],
             evidence: list[dict], usage: Usage, top_k: int = 3) -> TiebreakAnswer:
    cands = [f for f, _ in ranking[:top_k]]
    lines = [f"Unit: {unit}", "", "Persistent findings (rule: pattern, fault days in 14, peer-adjusted hours/day):"]
    lines += [f"- {f.rule}: {f.pattern}, {f.fault_days} days, {f.hours_per_day} h/day" for f in findings]
    lines += ["", "Statistical ranking:"] + [f"- {f}: {p:.0%}" for f, p in ranking[:top_k]]
    lines += ["", "Evidence (operating hours, mean over the lookback):", "measure | this unit | siblings' median"]
    lines += [f"{e['measure']} | {e['unit']} | {e['siblings_median']}" for e in evidence]
    lines += ["", f"Candidates: {', '.join(cands)}"]
    answer = llm.structured(TIEBREAK_SYSTEM, "\n".join(lines), TiebreakAnswer, usage)
    if answer.fault not in cands and answer.fault != NONE:
        answer.fault = cands[0]  # off-list answers fall back to the statistical choice
    return answer


@dataclass
class Diagnosis:
    fault: str
    probability: float
    ranking: list[tuple[str, float]]
    method: str                       # "signature" or "signature+model"
    explanation: str = ""


def diagnose(model: SignatureModel, signature: frozenset[str], margin: float,
             tiebreaker=None, none_threshold: float = 0.5) -> Diagnosis:
    """Rank with the signature model and decide.

    It's noise (NONE) if P(none) >= none_threshold. Otherwise the most likely real fault,
    with `tiebreaker(ranking)` consulted only when the top two real faults are within `margin`.
    """
    full = model.rank(signature)
    p_none = dict(full).get(NONE, 0.0)
    if p_none >= none_threshold:
        return Diagnosis(NONE, p_none, full, "signature")
    ranking = [x for x in full if x[0] != NONE] or full
    top, p = ranking[0]
    if tiebreaker and len(ranking) > 1 and p - ranking[1][1] < margin:
        answer = tiebreaker(ranking)
        prob = dict(ranking).get(answer.fault, 0.0)
        return Diagnosis(answer.fault, prob, full, "signature+model", answer.explanation)
    return Diagnosis(top, p, full, "signature")
