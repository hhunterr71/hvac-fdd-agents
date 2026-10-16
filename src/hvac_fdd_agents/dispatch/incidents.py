"""Turn Candidate Windows into Incidents with a Persistence pattern, as of a date.

Deterministic. Steps:
  1. daily fault minutes per (unit, rule, day), splitting windows at midnight;
  2. peer adjustment: a unit with identical siblings only counts minutes above its
     siblings for that rule and day ("worst": above the worst sibling, so only a box
     that stands out from all of them counts; "median": above the middle one). Noise
     that hits every box equally (e.g. a comfort rule through night setback) cancels;
  3. Persistence per (unit, rule) over the lookback: continuous, daily, weekly,
     sporadic, stale (thresholds in config/dispatch.yaml);
  4. one Incident per unit, holding all its findings.
"""

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yaml

from hvac_fdd_agents.data.points_config import PointsConfig
from hvac_fdd_agents.data.trend import quote
from hvac_fdd_agents.rules.roles import best_match
from hvac_fdd_agents.tagging.siblings import groups

DISPATCHABLE_DEFAULT = ("continuous", "daily", "weekly")


@dataclass
class DispatchConfig:
    lookback_days: int = 14
    fault_day_min_minutes: float = 60
    continuous_share: float = 0.5
    daily_min_fault_days: int = 7
    weekly_requires_every_week: bool = True
    stale_after_days: int = 9
    dispatch: tuple[str, ...] = DISPATCHABLE_DEFAULT
    peer_baseline: str = "worst"                 # "worst" or "median" sibling
    none_threshold: float = 0.5                  # dispatch only if P(noise) is below this


def load_config(path: Path) -> DispatchConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    p = raw["persistence"]
    return DispatchConfig(
        lookback_days=raw["lookback_days"], fault_day_min_minutes=raw["fault_day_min_minutes"],
        continuous_share=p["continuous_min_share_of_operating_hours"],
        daily_min_fault_days=p["daily_min_fault_days"], weekly_requires_every_week=p["weekly_requires_every_week"],
        stale_after_days=p["stale_after_days"], dispatch=tuple(raw["dispatch"]),
        peer_baseline=raw.get("peer_baseline", "worst"),
        none_threshold=raw.get("none_threshold", 0.5),
    )


@dataclass
class Finding:
    rule: str
    pattern: str                 # continuous / daily / weekly / sporadic / stale
    fault_days: int
    hours_per_day: float         # mean peer-adjusted fault hours per lookback day


@dataclass
class Incident:
    unit: str
    as_of: pd.Timestamp
    findings: list[Finding] = field(default_factory=list)

    def persistent(self, cfg: DispatchConfig) -> list[Finding]:
        return [f for f in self.findings if f.pattern in cfg.dispatch]

    def dispatchable(self, cfg: DispatchConfig) -> bool:
        return bool(self.persistent(cfg))

    def signature(self, cfg: DispatchConfig) -> frozenset[str]:
        return frozenset(f.rule for f in self.persistent(cfg))

    def magnitude(self, cfg: DispatchConfig) -> float:
        return max((f.hours_per_day for f in self.persistent(cfg)), default=0.0)

    def pattern(self, cfg: DispatchConfig) -> str:
        order = ["continuous", "daily", "weekly", "sporadic", "stale"]
        pats = [f.pattern for f in self.findings]
        return next((p for p in order if p in pats), "none")


def peers_for(cfg: PointsConfig) -> dict[str, list[str]]:
    """Each unit's identical siblings (Sibling Units), for peer comparison."""
    units = [{"id": u, "type": x.type, "points": [p for p, q in cfg.points.items() if q.unit == u]}
             for u, x in cfg.units.items()]
    out = {}
    for g in groups(units, kinds={}):
        members = [g["rep"]["id"]] + [s["unit"]["id"] for s in g["siblings"]]
        for m in members:
            out[m] = [x for x in members if x != m]
    return out


def operating_minutes(con, cfg: PointsConfig) -> tuple[pd.Series, str | None]:
    """Daily minutes the air handler fan runs (the 'operating hours' for Persistence), and the point used.

    `con` holds the Trend File as table `trend`. With no fan status point, every minute counts.
    Rows are scaled by the sample interval, so 5- or 15-minute data counts minutes too.
    """
    ahu = next((u for u, x in cfg.units.items() if x.type.startswith(("ahu", "rtu"))), None)
    point = best_match(cfg, ahu, ["discharge", "fan", "run", "sensor"]) if ahu else None
    step = con.execute("select median(d) from (select epoch(ts) - epoch(lag(ts) over (order by ts)) d "
                       "from trend)").fetchone()[0] or 60
    on = "1" if point is None else f"({quote(point)} > 0.5)::int"
    df = con.execute(f"select date_trunc('day', ts) d, sum({on}) m from trend group by 1 order by 1").df()
    return pd.Series(df.m.astype(float).values * step / 60, index=pd.DatetimeIndex(df.d)), point


def daily_minutes(windows: pd.DataFrame) -> pd.DataFrame:
    """Fault minutes per unit, rule, and calendar day. Windows: unit, rule, start, end, minutes."""
    rows: dict[tuple, float] = {}
    for unit, rule, start, end in windows[["unit", "rule", "start", "end"]].itertuples(index=False):
        s, e = pd.Timestamp(start), pd.Timestamp(end) + pd.Timedelta(minutes=1)
        while s < e:
            day = s.normalize()
            seg_end = min(e, day + pd.Timedelta(days=1))
            key = (unit, rule, day)
            rows[key] = rows.get(key, 0.0) + (seg_end - s).total_seconds() / 60
            s = seg_end
    if not rows:
        return pd.DataFrame(columns=["unit", "rule", "day", "minutes"])
    idx = pd.MultiIndex.from_tuples(rows.keys(), names=["unit", "rule", "day"])
    return pd.Series(list(rows.values()), index=idx, name="minutes").reset_index()


def peer_adjust(daily: pd.DataFrame, peers: dict[str, list[str]], baseline: str = "worst") -> pd.DataFrame:
    """Minutes above a unit's siblings (same rule, same day): above the worst one, or the median. No siblings: unchanged."""
    if daily.empty:
        return daily.assign(excess=[])
    wide = daily.pivot_table(index=["rule", "day"], columns="unit", values="minutes", aggfunc="sum")
    for u in {u for ps in peers.values() for u in ps} | set(peers):
        if u not in wide.columns:
            wide[u] = 0.0
    wide = wide.fillna(0.0)
    excess = wide.copy()
    for unit in wide.columns:
        others = [p for p in peers.get(unit, []) if p in wide.columns]
        if others:
            ref = wide[others].max(axis=1) if baseline == "worst" else wide[others].median(axis=1)
            excess[unit] = (wide[unit] - ref).clip(lower=0)
    out = excess.stack().rename("excess").reset_index()
    out.columns = ["rule", "day", "unit", "excess"]
    return out[out.excess > 0]


def classify(days: pd.Series, as_of: pd.Timestamp, operating_minutes: float, cfg: DispatchConfig) -> str | None:
    """Persistence of one finding. `days`: excess minutes indexed by day, within the lookback."""
    fault_days = days[days >= cfg.fault_day_min_minutes].index
    if len(fault_days) == 0:
        return None
    last_day = as_of.normalize()
    if (last_day - max(fault_days)).days >= cfg.stale_after_days:
        return "stale"
    if operating_minutes > 0 and days.sum() >= cfg.continuous_share * operating_minutes:
        return "continuous"
    if len(fault_days) >= cfg.daily_min_fault_days:
        return "daily"
    if cfg.weekly_requires_every_week:
        first_week = last_day - pd.Timedelta(days=7)
        if any(d <= first_week for d in fault_days) and any(d > first_week for d in fault_days):
            return "weekly"
    return "sporadic"


def incidents_as_of(excess: pd.DataFrame, as_of: pd.Timestamp, units: list[str],
                    operating: pd.Series, cfg: DispatchConfig) -> dict[str, Incident]:
    """One Incident per unit as of `as_of` (end of that day), over the lookback window."""
    end = as_of.normalize()
    start = end - pd.Timedelta(days=cfg.lookback_days - 1)
    window = excess[(excess.day >= start) & (excess.day <= end)]
    op_minutes = float(operating[(operating.index >= start) & (operating.index <= end)].sum())
    out = {u: Incident(u, end) for u in units}
    for (unit, rule), g in window.groupby(["unit", "rule"]):
        if unit not in out:
            continue
        days = g.set_index("day").excess
        pattern = classify(days, end, op_minutes, cfg)
        if pattern:
            out[unit].findings.append(Finding(
                rule, pattern, int((days >= cfg.fault_day_min_minutes).sum()),
                round(days.sum() / 60 / cfg.lookback_days, 2)))
    return out
