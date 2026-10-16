"""Case-level detection scoring for rule outcomes (rules only).

A rule *responds* to a fault when its confirmed fault hours on a unit exceed the same
unit's hours in the fault-free twin by at least MIN_EXCESS_HOURS. This is an evaluation
lens (it needs the twin, which a real building doesn't have); it answers "is this rule
sensitive to this fault?". Minute-level Symptom Window scoring builds on it later.

Per faulted case:
  detected        some rule responds on the faulted unit
  noisy_healthy   some rule responds on a healthy terminal unit (the AHU is reported
                  separately, since faults legitimately show up upstream)
Per system:
  background      confirmed fault hours on the fault-free twin: the false-alarm load
  coverage        fault type x rule: share of severities where the rule responds
"""

from dataclasses import dataclass

import pandas as pd

MIN_EXCESS_HOURS = 24.0


@dataclass
class DetectionScore:
    cases: pd.DataFrame          # one row per faulted case
    background: pd.DataFrame     # rule x unit fault hours on the twin
    coverage: pd.DataFrame       # fault type x rule

    def summary(self) -> str:
        c = self.cases
        lines = [f"Faulted cases: {len(c)}",
                 f"Detected (a rule responds on the faulted unit): {c.detected.mean():.0%}",
                 f"Healthy terminal units with a responding rule: {c.noisy_healthy.mean():.0%} of cases",
                 f"AHU responds: {c.ahu_responds.mean():.0%} of cases",
                 f"Background (fault-free twin): {self.background.values.sum():,.0f} confirmed fault hours "
                 f"from {(self.background.sum(axis=1) > 0).sum()} rules"]
        return "\n".join(lines)


def score(outcomes: pd.DataFrame, catalog: pd.DataFrame, system: str) -> DetectionScore:
    """`outcomes`: case, unit, rule, fault_hours. `catalog`: the LBNL catalog rows for `system`."""
    cat = catalog[catalog.system == system].set_index("file")
    twin_file = cat.index[cat.fault_free == "True"][0]
    hours = outcomes.pivot_table(index=["case", "unit"], columns="rule", values="fault_hours",
                                 aggfunc="sum").fillna(0.0)
    twin = hours.xs(twin_file, level="case")
    rows, responses = [], []
    for case, info in cat[cat.fault_free == "False"].iterrows():
        if case not in hours.index.get_level_values("case"):
            continue
        excess = hours.xs(case, level="case").sub(twin, fill_value=0.0)
        responds = excess >= MIN_EXCESS_HOURS
        faulted = info["unit"]
        terminals = [u for u in responds.index if u != faulted and not u.startswith("AHU")]
        hit_rules = sorted(responds.columns[responds.loc[faulted]]) if faulted in responds.index else []
        rows.append({
            "case": case, "fault": f"{info['component']}_{info['mode']}", "severity": info["severity"],
            "unit": faulted, "detected": bool(hit_rules), "rules": " ".join(hit_rules),
            "noisy_healthy": bool(responds.loc[terminals].any().any()) if terminals else False,
            "ahu_responds": bool(responds.loc[[u for u in responds.index if u.startswith("AHU")]].any().any()),
        })
        for r in responds.columns:
            responses.append({"fault": rows[-1]["fault"], "rule": r,
                              "responds": bool(responds.loc[faulted, r]) if faulted in responds.index else False})
    cases = pd.DataFrame(rows)
    coverage = (pd.DataFrame(responses).pivot_table(index="fault", columns="rule", values="responds", aggfunc="mean")
                if responses else pd.DataFrame())
    coverage = coverage.loc[:, coverage.sum() > 0] if not coverage.empty else coverage
    background = twin.T
    return DetectionScore(cases, background, coverage)
