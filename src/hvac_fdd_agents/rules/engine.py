"""Run the Fault Book on a tagged Trend File and return Candidate Windows.

For each unit: resolve open-fdd roles from its Haystack tags, build a role-named frame,
run every enabled rule for its equipment kind, and turn each confirmed fault run into a
Candidate Window (unit, rule, start, end). Deterministic: no model calls.
"""

from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from hvac_fdd_agents.data import trend
from hvac_fdd_agents.data.points_config import PointsConfig
from hvac_fdd_agents.data.trend import quote
from hvac_fdd_agents.rules import roles
from hvac_fdd_agents.rules.faultbook import FaultBook

POLL_SECONDS = 60.0
OPEN_FDD_TYPE = {"vav": "VAV", "ahu": "AHU"}


@dataclass
class CandidateWindow:
    unit: str
    rule: str
    start: pd.Timestamp
    end: pd.Timestamp
    minutes: float


@dataclass
class RuleOutcome:
    unit: str
    rule: str
    status: str                 # PASS / FAULT / SKIPPED_MISSING_ROLES / ...
    fault_hours: float
    missing_roles: list[str]


def run(trend_file: Path, cfg: PointsConfig, book: FaultBook, role_map: dict,
        units: list[str] | None = None, con=None) -> tuple[list[CandidateWindow], list[RuleOutcome]]:
    """Run the Fault Book on every unit. Pass `con` (table `trend` already loaded) to skip reloading."""
    from open_fdd.rules import RULES_BY_ID
    from open_fdd.rules.runner import run_cookbook_rule

    con = con or trend.load(trend_file)[0]
    windows, outcomes = [], []
    for unit in units or list(cfg.units):
        kind = roles.KIND.get(cfg.units[unit].type)
        enabled = book.enabled(kind) if kind else {}
        if not enabled:
            continue
        mapping = roles.resolve(cfg, unit, role_map)
        frame = _frame(con, mapping)
        for rule_id, params in enabled.items():
            res = run_cookbook_rule(
                RULES_BY_ID[rule_id], frame, equipment_id=unit, equipment_kind=kind,
                equipment_type=OPEN_FDD_TYPE[kind], poll_seconds=POLL_SECONDS,
                params_by_rule={rule_id: params},
            )
            outcomes.append(RuleOutcome(unit, rule_id, res.status, float(res.fault_hours or 0.0),
                                        list(res.missing_roles or [])))
            if res.confirmed_fault is not None and res.status == "FAULT":
                windows += _windows(res.confirmed_fault, unit, rule_id)
    return windows, outcomes


def _frame(con, mapping: dict[str, str]) -> pd.DataFrame:
    if not mapping:
        return pd.DataFrame(index=con.execute("select ts from trend").df()["ts"])
    cols = ", ".join(f"{quote(point)} as {quote(role)}" for role, point in mapping.items())
    return con.execute(f"select ts, {cols} from trend order by ts").df().set_index("ts")


def _windows(mask: pd.Series, unit: str, rule: str) -> list[CandidateWindow]:
    mask = mask.fillna(False).astype(bool)
    if not mask.any():
        return []
    run_id = (mask != mask.shift()).cumsum()
    out = []
    for _, seg in mask[mask].groupby(run_id[mask]):
        start, end = seg.index[0], seg.index[-1]
        out.append(CandidateWindow(unit, rule, start, end, (end - start).total_seconds() / 60 + POLL_SECONDS / 60))
    return out


def to_frames(windows: list[CandidateWindow], outcomes: list[RuleOutcome]) -> tuple[pd.DataFrame, pd.DataFrame]:
    return pd.DataFrame([asdict(w) for w in windows]), pd.DataFrame([asdict(o) for o in outcomes])
