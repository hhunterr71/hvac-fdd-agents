"""Backtest a Fault Book: its rules -> Incidents -> diagnosis -> Work Orders -> Work Order Outcomes, scored.

Uses the rule cache, so only rule settings never run before cost time. The rest is the
phase 4 pipeline, unchanged: peer comparison, Persistence at each month end, the
signature model (fit on the tuning cases, or given), Work Orders with the policy gate.

Each dispatched Work Order closes with a Work Order Outcome, the stand-in for a
technician's close-out report:
  confirmed         right unit, right fault
  wrong_diagnosis   the unit is faulty, but not with the diagnosed fault
  no_fault_found    the unit is healthy (an unnecessary dispatch)
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field

import pandas as pd

from hvac_fdd_agents.data.points_config import AIR_HANDLERS, PointsConfig
from hvac_fdd_agents.dispatch import diagnose as dx
from hvac_fdd_agents.dispatch import incidents as inc
from hvac_fdd_agents.dispatch import workorder as wo
from hvac_fdd_agents.rules.faultbook import FaultBook
from hvac_fdd_agents.tuning.cache import Case, RuleCache, requests_for


@dataclass
class Row:
    """One unit's Incident in one case, as of one date, with the truth about it."""
    case: str
    unit: str
    as_of: pd.Timestamp
    incident: inc.Incident
    label: str                    # fault type if this unit is the faulty one, else "none"
    faulted_unit: str | None


@dataclass
class Dispatch:
    row: Row
    order: wo.WorkOrder
    outcome: str                  # confirmed / wrong_diagnosis / no_fault_found


@dataclass
class Result:
    metrics: dict
    dispatches: list[Dispatch]
    rows: list[Row]
    model: dx.SignatureModel
    rules: list[dict] = field(default_factory=list)   # per-rule report (see rule_report)


def month_ends(days: pd.DatetimeIndex) -> list[pd.Timestamp]:
    """Every month end inside the data period: the dates dispatch is decided at."""
    first, last = days.min(), days.max()
    ends = pd.date_range(first, last, freq="ME")
    return [d for d in ends if d >= first + pd.Timedelta(days=13)]


class Backtester:
    def __init__(self, cache: RuleCache, cfg: PointsConfig, cases: list[Case], dcfg: inc.DispatchConfig,
                 remediation: dict, policy: dict):
        self.cache, self.cfg, self.cases = cache, cfg, cases
        self.dcfg, self.remediation, self.policy = dcfg, remediation, policy
        self.peers = inc.peers_for(cfg)
        self.ahus = {u for u, x in cfg.units.items() if x.type in AIR_HANDLERS}

    def rows(self, book: FaultBook, log=print) -> list[Row]:
        reqs = requests_for(book)
        self.cache.ensure(reqs, log=log)
        frames = [self.cache.daily(r) for r in reqs]
        daily = pd.concat(frames) if frames else pd.DataFrame(columns=["case", "unit", "day", "minutes", "rule"])
        by_case = dict(tuple(daily.groupby("case")))
        op = self.cache.operating()
        op_by_case = dict(tuple(op.groupby("case")))
        out = []
        for case in self.cases:
            d = by_case.get(case.name, daily.iloc[0:0])[["unit", "rule", "day", "minutes"]]
            excess = inc.peer_adjust(d, self.peers, self.dcfg.peer_baseline)
            o = op_by_case[case.name]
            operating = pd.Series(o.minutes.values, index=pd.DatetimeIndex(o.day))
            for as_of in month_ends(operating.index):
                for unit, incident in inc.incidents_as_of(excess, as_of, list(self.cfg.units), operating,
                                                          self.dcfg).items():
                    label = case.fault if case.faulted_unit and unit == case.faulted_unit else dx.NONE
                    out.append(Row(case.name, unit, as_of, incident, label, case.faulted_unit))
        return out

    def fit(self, rows: list[Row]) -> dx.SignatureModel:
        return dx.SignatureModel.fit([(r.incident.signature(self.dcfg), r.label) for r in rows
                                      if r.incident.dispatchable(self.dcfg)])

    def dispatch(self, rows: list[Row], model: dx.SignatureModel) -> list[Dispatch]:
        out = []
        for r in rows:
            if not r.incident.dispatchable(self.dcfg):
                continue
            d = dx.diagnose(model, r.incident.signature(self.dcfg), dx.MARGIN,
                            none_threshold=self.dcfg.none_threshold)
            order = wo.build(r.incident, d, self.dcfg, self.remediation, self.policy)
            if order is None:
                continue
            outcome = ("no_fault_found" if r.label == dx.NONE
                       else "confirmed" if order.fault == r.label else "wrong_diagnosis")
            out.append(Dispatch(r, order, outcome))
        return out

    def run(self, book: FaultBook, model: dx.SignatureModel | None = None, log=print) -> Result:
        """Backtest `book`. Without `model`, fit the signature model on these cases first (tuning)."""
        rows = self.rows(book, log=log)
        model = model or self.fit(rows)
        dispatches = self.dispatch(rows, model)
        metrics = score(rows, dispatches, self.remediation, self.ahus)
        return Result(metrics, dispatches, rows, model, rule_report(self.cache, book, rows, dispatches, self.dcfg))


def score(rows: list[Row], dispatches: list[Dispatch], remediation: dict, ahus: set[str]) -> dict:
    """The phase 4 metrics, plus Work Order Outcome counts and the tuning objective."""
    by_key = {(d.row.case, d.row.unit, d.row.as_of): d for d in dispatches}
    faulted_cases = {r.case for r in rows if r.faulted_unit}
    detected = {d.row.case for d in dispatches if d.row.faulted_unit and d.row.unit == d.row.faulted_unit}
    on_faulty = [d for d in dispatches if d.row.faulted_unit and d.row.unit == d.row.faulted_unit]
    healthy = [r for r in rows if r.unit != r.faulted_unit]
    terminal = [r for r in healthy if r.unit not in ahus]
    unnecessary = [r for r in healthy if (r.case, r.unit, r.as_of) in by_key]
    unnecessary_terminal = [r for r in unnecessary if r.unit not in ahus]
    right_fix = 0
    for d in on_faulty:
        t = remediation["faults"].get(d.row.label, {})
        right_fix += (d.order.trade, d.order.action) == (t.get("trade"), t.get("action"))
    caught = len(detected) / len(faulted_cases) if faulted_cases else 0.0
    false_rate = 100 * len(unnecessary_terminal) / len(terminal) if terminal else 0.0
    outcomes = Counter(d.outcome for d in dispatches)
    return {
        "faulted_cases": len(faulted_cases),
        "detected_cases": len(detected),
        "detection_rate": round(caught, 3),
        "diagnosis_top1": round(sum(d.order.fault == d.row.label for d in on_faulty) / len(on_faulty), 3) if on_faulty else None,
        "right_fix": round(right_fix / len(on_faulty), 3) if on_faulty else None,
        "faulted_unit_dispatches": len(on_faulty),
        "unnecessary_dispatches": len(unnecessary),
        "unnecessary_ahu": len(unnecessary) - len(unnecessary_terminal),
        "unnecessary_terminal_per_100_unit_months": round(false_rate, 1),
        "outcomes": {k: outcomes.get(k, 0) for k in ("confirmed", "wrong_diagnosis", "no_fault_found")},
        # One number to tune against: faults caught (%) minus unnecessary terminal dispatches per 100 box-months.
        "objective": round(100 * caught - false_rate, 1),
    }


def rule_report(cache: RuleCache, book: FaultBook, rows: list[Row], dispatches: list[Dispatch],
                dcfg: inc.DispatchConfig) -> list[dict]:
    """Per rule: how much it fires, where it persists, and the outcomes of the dispatches it drove."""
    persist_faulty, persist_healthy = Counter(), Counter()
    for r in rows:
        for f in r.incident.persistent(dcfg):
            (persist_faulty if r.label != dx.NONE else persist_healthy)[f.rule] += 1
    drove = defaultdict(Counter)
    for d in dispatches:
        for rule in d.row.incident.signature(dcfg):
            drove[rule][d.outcome] += 1
    out = []
    for kind, rule, params in requests_for(book):
        o = cache.outcomes((kind, rule, params))
        out.append({
            "kind": kind, "rule": rule, "params": params,
            "fault_hours": round(float(o.fault_hours.sum()), 1),
            "skipped_units": int(o.status.str.startswith("SKIPPED").sum()),
            "persistent_on_faulty_unit_months": persist_faulty[rule],
            "persistent_on_healthy_unit_months": persist_healthy[rule],
            "dispatches": dict(drove[rule]),
        })
    return sorted(out, key=lambda x: -x["fault_hours"])
