"""Data Health: is the data arriving, and can it be trusted? (config/health.yaml)

Deterministic checks over a Trend File loaded as table `trend` (see trend.load):

- the file as a whole: sample interval, and stretches where no rows arrive at all;
- each point: coverage (share of expected samples with a value), the longest gap with no
  value, the longest stretch where the value doesn't change (sensors only: setpoints and
  commands sit still legitimately), and values outside a plausible range for its kind.

Each point gets a status (ok / warning / bad) and plain-language issues, rolled up per unit.
"""

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

from hvac_fdd_agents.data.points_config import PointsConfig
from hvac_fdd_agents.data.trend import quote

STATUS_ORDER = {"ok": 0, "warning": 1, "bad": 2}


@dataclass
class HealthConfig:
    coverage_warning: float = 0.98
    coverage_bad: float = 0.90
    gap_warning_h: float = 1
    gap_bad_h: float = 4
    flat_warning_h: float = 6
    flat_bad_h: float = 24
    range_warning: float = 0.001
    range_bad: float = 0.01
    flat_tags: tuple = (frozenset({"temp"}), frozenset({"humidity"}))
    ranges: tuple = ()


def load_config(path: Path) -> HealthConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return HealthConfig(
        coverage_warning=raw["coverage"]["warning_below"], coverage_bad=raw["coverage"]["bad_below"],
        gap_warning_h=raw["longest_gap_hours"]["warning"], gap_bad_h=raw["longest_gap_hours"]["bad"],
        flat_warning_h=raw["flatline_hours"]["warning"], flat_bad_h=raw["flatline_hours"]["bad"],
        range_warning=raw["out_of_range_share"]["warning"], range_bad=raw["out_of_range_share"]["bad"],
        flat_tags=tuple(frozenset(t) for t in raw.get("flatline_tags", [["temp"], ["humidity"]])),
        ranges=tuple((frozenset(r["tags"]), r["min"], r["max"]) for r in raw.get("ranges", [])),
    )


def check_file(con) -> dict:
    """Period, sample interval, and gaps where no rows arrived at all."""
    start, end, rows = con.execute("select min(ts), max(ts), count(*) from trend").fetchone()
    step = con.execute("select median(d) from (select epoch(ts) - epoch(lag(ts) over (order by ts)) d "
                       "from trend)").fetchone()[0] or 60.0
    gaps = con.execute(f"""
        select prev + to_seconds({step}) as gap_start, n * {step} / 3600.0 as hours from (
          select lag(ts) over (order by ts) as prev,
                 round((epoch(ts) - epoch(lag(ts) over (order by ts))) / {step}) - 1 as n from trend)
        where n >= 1 order by hours desc""").df()
    expected = int(round((pd.Timestamp(end) - pd.Timestamp(start)).total_seconds() / step)) + 1
    return {
        "start": str(start), "end": str(end), "rows": int(rows), "interval_minutes": round(step / 60, 2),
        "expected_rows": expected, "missing_rows": max(0, expected - int(rows)),
        "row_gaps": int(len(gaps)),
        "longest_row_gap_hours": round(float(gaps.hours.iloc[0]), 2) if len(gaps) else 0.0,
        "longest_row_gap_start": str(gaps.gap_start.iloc[0]) if len(gaps) else None,
    }


def kind_of(tags: list[str], kept: bool, mapped: bool) -> str:
    if not mapped:
        return "unmapped"
    if not kept:
        return "excluded"
    for k in ("sensor", "sp", "cmd"):
        if k in tags:
            return k
    return "other"


def _range_for(tags: list[str], hcfg: HealthConfig):
    t = set(tags)
    return next(((lo, hi) for want, lo, hi in hcfg.ranges if want <= t), None)


def check_point(con, point: str, step: float, start, end, tags: list[str], kind: str,
                hcfg: HealthConfig) -> dict:
    c = quote(point)
    con.execute(f"""
        create or replace temp table _runs as
        with s as (select ts, {c}::double v from trend where {c} is not null),
        l as (select ts, v, case when v = lag(v) over (order by ts) then 0 else 1 end chg from s),
        g as (select ts, v, sum(chg) over (order by ts rows unbounded preceding) grp from l)
        select grp, any_value(v) v, min(ts) a, max(ts) b, count(*) n from g group by grp""")
    n, distinct, first_ts, last_ts = con.execute(
        "select coalesce(sum(n), 0), count(distinct v), min(a), max(b) from _runs").fetchone()
    expected = (pd.Timestamp(end) - pd.Timestamp(start)).total_seconds() / step + 1
    row = {"samples": int(n), "coverage": round(n / expected, 4) if expected else 0.0, "distinct_values": int(distinct)}
    if n == 0:
        return {**row, "longest_gap_hours": round(expected * step / 3600, 2), "flat_hours": None,
                "flat_value": None, "last_change": None, "out_of_range_share": None}
    inner = con.execute(f"""
        select max(d) from (select epoch(ts) - epoch(lag(ts) over (order by ts)) - {step} d
                            from trend where {c} is not null)""").fetchone()[0] or 0.0
    edges = max((pd.Timestamp(first_ts) - pd.Timestamp(start)).total_seconds(),
                (pd.Timestamp(end) - pd.Timestamp(last_ts)).total_seconds())
    row["longest_gap_hours"] = round(max(inner, edges) / 3600, 2)
    flat = con.execute(f"select v, a, epoch(b) - epoch(a) + {step} from _runs order by 3 desc limit 1").fetchone()
    row["flat_hours"] = round(flat[2] / 3600, 2)
    row["flat_value"] = flat[0]
    row["flat_start"] = str(flat[1])
    row["last_change"] = str(con.execute("select a from _runs order by grp desc limit 1").fetchone()[0])
    rng = _range_for(tags, hcfg) if kind not in ("unmapped", "excluded") else None
    if rng:
        bad = con.execute(f"select sum(n) from _runs where v < {rng[0]} or v > {rng[1]}").fetchone()[0] or 0
        row["out_of_range_share"] = round(bad / n, 4)
    else:
        row["out_of_range_share"] = None
    return row


def judge(row: dict, kind: str, hcfg: HealthConfig, tags: list[str] = ()) -> tuple[str, list[str]]:
    """Status and plain-language issues for one point's checks."""
    issues: list[tuple[str, str]] = []
    if row["samples"] == 0:
        return "bad", ["no data at all"]
    cov = row["coverage"]
    if cov < hcfg.coverage_warning:
        issues.append(("bad" if cov < hcfg.coverage_bad else "warning", f"only {cov:.0%} of expected samples"))
    gap = row["longest_gap_hours"]
    if gap >= hcfg.gap_warning_h:
        issues.append(("bad" if gap >= hcfg.gap_bad_h else "warning", f"no data for {gap:g} h at a stretch"))
    if kind == "sensor" and any(t <= set(tags) for t in hcfg.flat_tags):
        if row["distinct_values"] == 1:
            issues.append(("warning", f"never changes (always {row['flat_value']:g}); check it is really measured"))
        elif row["distinct_values"] > 2 and row["flat_hours"] >= hcfg.flat_warning_h:
            sev = "bad" if row["flat_hours"] >= hcfg.flat_bad_h else "warning"
            issues.append((sev, f"stuck at {row['flat_value']:g} for {row['flat_hours']:g} h "
                                f"(from {row['flat_start'][:16]})"))
    oor = row["out_of_range_share"]
    if oor is not None and oor > hcfg.range_warning:
        issues.append(("bad" if oor > hcfg.range_bad else "warning", f"{oor:.2%} of values outside the plausible range"))
    status = max((s for s, _ in issues), key=STATUS_ORDER.get, default="ok")
    return status, [text for _, text in issues]


def check_points(con, cfg: PointsConfig, hcfg: HealthConfig, file: dict) -> pd.DataFrame:
    """One row per numeric Trend File column: unit, kind, checks, status, issues."""
    cols = con.execute("select column_name, column_type from (describe trend) where column_name <> 'ts'").fetchall()
    step = file["interval_minutes"] * 60
    rows = []
    for point, dtype in cols:
        p = cfg.points.get(point)
        tags = p.tags if p else []
        kind = kind_of(tags, p.kept if p else False, p is not None)
        base = {"point": point, "unit": p.unit if p else None, "kind": kind, "tags": " ".join(tags)}
        if dtype not in ("DOUBLE", "FLOAT", "BIGINT", "INTEGER", "SMALLINT", "TINYINT", "HUGEINT", "DECIMAL",
                         "UBIGINT", "UINTEGER") and not dtype.startswith("DECIMAL"):
            rows.append({**base, "status": "warning", "issues": f"not numeric ({dtype})"})
            continue
        row = check_point(con, point, step, file["start"], file["end"], tags, kind, hcfg)
        status, issues = judge(row, kind, hcfg, tags)
        rows.append({**base, **row, "status": status, "issues": "; ".join(issues)})
    con.execute("drop table if exists _runs")
    return pd.DataFrame(rows)


def by_unit(points: pd.DataFrame) -> dict[str, dict]:
    """Per unit: worst status, counts by status, and the problem points."""
    out = {}
    for unit, g in points[points.unit.notna()].groupby("unit"):
        counts = g.status.value_counts().to_dict()
        worst = max(g.status, key=STATUS_ORDER.get)
        problems = g[g.status != "ok"].sort_values("status", key=lambda s: -s.map(STATUS_ORDER))
        out[unit] = {"status": worst, "points": int(len(g)),
                     "counts": {k: int(counts.get(k, 0)) for k in STATUS_ORDER},
                     "problems": [f"{r.point}: {r.issues}" for r in problems.itertuples()]}
    return out
