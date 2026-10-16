"""Point Profiles: a compact statistical summary of each point, computed in DuckDB.

Agents never see raw telemetry. They get one line per point: range, typical values,
how often it changes, whether it's continuous or stepped, and which other points it
moves with. That is what an engineer looks at when tagging from a trend.
"""

from dataclasses import dataclass, field

import duckdb

from hvac_fdd_agents.data.trend import quote

MAX_LISTED_VALUES = 6       # list exact values for points with this many or fewer
CORR_THRESHOLD = 0.85       # report correlated points at or above this |r| (hourly means)
MAX_CORRELATED = 3
CORR_BATCH = 400            # pairs per correlation query
CORR_MIN_HOURS = 24         # fewer hourly samples than this and correlations are meaningless


@dataclass
class PointProfile:
    point: str
    count: int
    nulls: int
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    p05: float | None = None
    p50: float | None = None
    p95: float | None = None
    distinct: int = 0
    values: list[float] | None = None        # exact values when few
    change_rate: float = 0.0                 # fraction of consecutive samples that differ
    correlated: list[tuple[str, float]] = field(default_factory=list)

    @property
    def constant(self) -> bool:
        return self.distinct <= 1

    @property
    def kind(self) -> str:
        """Profile shape, used to check that 'identical' units really behave alike."""
        if self.count == 0:
            return "empty"
        if self.constant:
            return "constant"
        if self.values is not None:
            return "discrete"   # exact values vary with sizing (e.g. fan pressure on a smaller box)
        return "continuous"

    def line(self) -> str:
        """One line for a prompt."""
        if self.count == 0:
            return f"{self.point}: no data"
        if self.constant:
            return f"{self.point}: constant {fmt(self.min)}"
        parts = [f"range {fmt(self.min)}..{fmt(self.max)}"]
        if self.values is not None:
            parts.append(f"{len(self.values)} values {{{', '.join(fmt(v) for v in self.values)}}}")
        else:
            parts.append(f"p5/p50/p95 {fmt(self.p05)}/{fmt(self.p50)}/{fmt(self.p95)}, mean {fmt(self.mean)}")
            parts.append(f"~{self.distinct} distinct values")
        parts.append(f"changes in {self.change_rate:.0%} of samples")
        if self.nulls:
            parts.append(f"{self.nulls / (self.count + self.nulls):.0%} missing")
        if self.correlated:
            parts.append("moves with " + ", ".join(f"{p} (r={r:+.2f})" for p, r in self.correlated))
        return f"{self.point}: " + "; ".join(parts)


def fmt(x: float | None) -> str:
    if x is None:
        return "?"
    if x == int(x) and abs(x) < 1e6:
        return str(int(x))
    return f"{x:.4g}"


def compute(con: duckdb.DuckDBPyConnection, points: list[str]) -> dict[str, PointProfile]:
    """Profile every point in table `trend` (see trend.load)."""
    numeric = _numeric(con, points)
    profiles = {p: PointProfile(point=p, count=0, nulls=0) for p in points}
    if not numeric:
        return profiles

    aggs = []
    for p in numeric:
        c = quote(p)
        aggs += [f"count({c})", f"count(*) - count({c})", f"min({c})", f"max({c})", f"avg({c})",
                 f"quantile_cont({c}, 0.05)", f"quantile_cont({c}, 0.5)", f"quantile_cont({c}, 0.95)",
                 f"approx_count_distinct({c})"]
    row = con.execute(f"select {', '.join(aggs)} from trend").fetchone()
    for i, p in enumerate(numeric):
        v = row[i * 9:(i + 1) * 9]
        prof = profiles[p]
        prof.count, prof.nulls = v[0], v[1]
        prof.min, prof.max, prof.mean, prof.p05, prof.p50, prof.p95 = (_f(x) for x in v[2:8])
        prof.distinct = v[8] or 0

    # Exact values and distinct counts for low-cardinality points (approx count can be off by one).
    for p in numeric:
        prof = profiles[p]
        if prof.distinct <= MAX_LISTED_VALUES + 2:
            vals = [r[0] for r in con.execute(
                f"select distinct {quote(p)} from trend where {quote(p)} is not null order by 1 limit {MAX_LISTED_VALUES + 1}"
            ).fetchall()]
            prof.distinct = len(vals)
            if len(vals) <= MAX_LISTED_VALUES:
                prof.values = [float(x) for x in vals]

    lags = ", ".join(f"({quote(p)} is distinct from lag({quote(p)}) over w)::int as {quote(p)}" for p in numeric)
    sums = ", ".join(f"avg({quote(p)})" for p in numeric)
    rates = con.execute(f"select {sums} from (select {lags} from trend window w as (order by ts))").fetchone()
    for p, r in zip(numeric, rates):
        profiles[p].change_rate = float(r or 0.0)

    _correlations(con, [p for p in numeric if not profiles[p].constant], profiles)
    return profiles


def _numeric(con, points: list[str]) -> list[str]:
    types = dict(con.execute("select column_name, column_type from (describe trend)").fetchall())
    ok = ("DOUBLE", "FLOAT", "BIGINT", "INTEGER", "SMALLINT", "TINYINT", "HUGEINT", "DECIMAL", "BOOLEAN")
    return [p for p in points if types.get(p, "").startswith(ok)]


def _f(x) -> float | None:
    return None if x is None else float(x)


def _correlations(con, points: list[str], profiles: dict[str, PointProfile]) -> None:
    """Find points that move together, on hourly means (cheap, and smooths 1-minute noise)."""
    if len(points) < 2:
        return
    cols = ", ".join(f"avg({quote(p)}::double) as {quote(p)}" for p in points)
    con.execute(f"create or replace temp table hourly as select {cols} from trend group by date_trunc('hour', ts)")
    if con.execute("select count(*) from hourly").fetchone()[0] < CORR_MIN_HOURS:
        return
    pairs = [(a, b) for i, a in enumerate(points) for b in points[i + 1:]]
    found: dict[str, list[tuple[str, float]]] = {p: [] for p in points}
    for start in range(0, len(pairs), CORR_BATCH):
        batch = pairs[start:start + CORR_BATCH]
        exprs = ", ".join(f"corr({quote(a)}, {quote(b)})" for a, b in batch)
        for (a, b), r in zip(batch, con.execute(f"select {exprs} from hourly").fetchone()):
            if r is not None and abs(r) >= CORR_THRESHOLD:
                found[a].append((b, r))
                found[b].append((a, r))
    for p, hits in found.items():
        profiles[p].correlated = sorted(hits, key=lambda h: -abs(h[1]))[:MAX_CORRELATED]
