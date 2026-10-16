"""Per-rule results, cached, so a Fault Book edit only reruns the rule it changes (ADR-0009).

Each rule runs independently, so its results on a set of cases depend only on the rule,
its parameters, and the data. For every (equipment kind, rule, parameters) the cache holds:

  <kind>/<rule>/<key>.parquet       fault minutes per case, unit, and day
  <kind>/<rule>/<key>.outcomes.csv  each unit's result (FAULT, PASS, SKIPPED_...) and fault hours
  operating.parquet                 the air handler's operating minutes per case and day
  timing.csv                        seconds per case and rule, to find slow spots

The cache lives in a folder named for the system and a fingerprint of everything that
changes results (the points config, the role map, the open-fdd version), so it never
serves stale results.
"""

import hashlib
import json
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from hvac_fdd_agents.data import points_config, trend
from hvac_fdd_agents.dispatch import incidents as inc
from hvac_fdd_agents.rules import engine, roles
from hvac_fdd_agents.rules.faultbook import FaultBook

Request = tuple[str, str, dict]          # (equipment kind, rule id, parameter overrides)


@dataclass(frozen=True)
class Case:
    """One Trend File to backtest on, with what is really wrong in it (if known)."""
    name: str
    trend: Path
    faulted_unit: str | None = None       # None: fault-free
    fault: str | None = None              # fault type, e.g. damper_stuck


def params_key(params: dict) -> str:
    if not params:
        return "default"
    return hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:12]


def requests_for(book: FaultBook) -> list[Request]:
    """Every (kind, rule, params) a Fault Book runs."""
    return [(kind, rid, params) for kind in book.rules for rid, params in book.enabled(kind).items()]


class RuleCache:
    def __init__(self, root: Path, system: str, points_file: Path, role_map_file: Path, cases: list[Case],
                 workers: int = 4):
        import open_fdd
        fingerprint = hashlib.sha1(
            Path(points_file).read_bytes() + Path(role_map_file).read_bytes()
            + getattr(open_fdd, "__version__", "?").encode()).hexdigest()[:10]
        self.dir = Path(root) / f"{system}-{fingerprint}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.points_file, self.role_map_file = Path(points_file), Path(role_map_file)
        self.cases = cases
        self.workers = workers

    def _base(self, kind: str, rule: str, params: dict) -> Path:
        return self.dir / kind / rule / params_key(params)

    def has(self, req: Request) -> bool:
        return self._base(*req).with_suffix(".parquet").exists()

    def ensure(self, reqs: list[Request], log=print) -> None:
        """Compute whatever isn't cached yet: each case is loaded once, missing rules run on it."""
        missing = [r for r in reqs if not self.has(r)]
        need_operating = not (self.dir / "operating.parquet").exists()
        if not missing and not need_operating:
            return
        log(f"running {len(missing)} rule setting(s) on {len(self.cases)} cases")
        jobs = [(c, str(self.points_file), str(self.role_map_file), missing, need_operating) for c in self.cases]
        daily = {params_key(p) + kind + rule: [] for kind, rule, p in missing}
        outcomes = {k: [] for k in daily}
        operating, timing = [], []
        with ProcessPoolExecutor(self.workers) as pool:
            for i, res in enumerate(pool.map(_run_case, jobs), 1):
                for k, rows in res["daily"].items():
                    daily[k] += rows
                for k, rows in res["outcomes"].items():
                    outcomes[k] += rows
                operating += res["operating"]
                timing += res["timing"]
                if i % 5 == 0 or i == len(jobs):
                    log(f"  {i}/{len(jobs)} cases")
        for kind, rule, p in missing:
            k = params_key(p) + kind + rule
            base = self._base(kind, rule, p)
            base.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(daily[k], columns=["case", "unit", "day", "minutes"]).to_parquet(base.with_suffix(".parquet"))
            pd.DataFrame(outcomes[k], columns=["case", "unit", "status", "fault_hours"]) \
                .to_csv(base.with_suffix(".outcomes.csv"), index=False)
            (base.with_suffix(".params.json")).write_text(json.dumps(p, sort_keys=True), encoding="utf-8")
        if need_operating:
            pd.DataFrame(operating, columns=["case", "day", "minutes"]).to_parquet(self.dir / "operating.parquet")
        tf = self.dir / "timing.csv"
        pd.DataFrame(timing, columns=["case", "kind", "rule", "seconds"]).to_csv(
            tf, mode="a", header=not tf.exists(), index=False)

    def daily(self, req: Request) -> pd.DataFrame:
        """case, unit, rule, day, minutes for one rule setting (must be cached)."""
        kind, rule, p = req
        df = pd.read_parquet(self._base(kind, rule, p).with_suffix(".parquet"))
        return df.assign(rule=rule)

    def outcomes(self, req: Request) -> pd.DataFrame:
        kind, rule, p = req
        return pd.read_csv(self._base(kind, rule, p).with_suffix(".outcomes.csv")).assign(rule=rule)

    def operating(self) -> pd.DataFrame:
        return pd.read_parquet(self.dir / "operating.parquet")


def _run_case(job) -> dict:
    """Worker: load one case, run the requested rule settings on every unit of a matching kind."""
    case, points_file, role_map_file, reqs, need_operating = job
    cfg = points_config.load(Path(points_file))
    role_map = roles.load_role_map(Path(role_map_file))
    con, _ = trend.load(case.trend)
    out = {"daily": {}, "outcomes": {}, "operating": [], "timing": []}
    if need_operating:
        op, _ = inc.operating_minutes(con, cfg)
        out["operating"] = [(case.name, d, m) for d, m in op.items()]
    for unit, u in cfg.units.items():
        kind = roles.KIND.get(u.type)
        mine = [r for r in reqs if r[0] == kind]
        if not mine:
            continue
        frame = engine.unit_frame(con, cfg, unit, role_map)
        for _, rule, params in mine:
            t = time.time()
            outcome, windows = engine.run_rule(frame, unit, kind, rule, params)
            k = params_key(params) + kind + rule
            out["outcomes"].setdefault(k, []).append((case.name, unit, outcome.status, outcome.fault_hours))
            if windows:
                d = inc.daily_minutes(pd.DataFrame([w.__dict__ for w in windows]))
                out["daily"].setdefault(k, []).extend(
                    (case.name, r.unit, r.day, r.minutes) for r in d.itertuples(index=False))
            out["timing"].append((case.name, kind, rule, round(time.time() - t, 2)))
    return out
