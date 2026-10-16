"""The chat's tools: read a Site Snapshot, and request (never approve) Work Orders.

Every number the chat reports comes from one of these. Each tool takes the Site and the
model's input dict, and returns something JSON-serializable. A ValueError is a mistake the
model can fix (unknown unit, bad date); its message goes back to the model as the result.
"""

import difflib
from functools import cache
from pathlib import Path

import pandas as pd

from hvac_fdd_agents.dispatch import diagnose as dx
from hvac_fdd_agents.dispatch import workorder as wo
from hvac_fdd_agents.paths import REMEDIATION
from hvac_fdd_agents.site.snapshot import Site

MAX_POINTS = 6
MAX_ROWS = 150


def _obj(props: dict, required: list[str] = ()) -> dict:
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


SCHEMAS = [
    {"name": "site_overview",
     "description": "The whole site at a glance: data period and as-of date, every unit with its status "
                    "(work_order / watch / ok), its diagnosis, its Work Order, and its Data Health status. "
                    "Start here for questions like 'how is my system doing?'.",
     "input_schema": _obj({})},
    {"name": "unit_details",
     "description": "Everything about one unit: type, what feeds it, its identical siblings, persistent and "
                    "sporadic rule findings, the diagnosis ranking, why it is or isn't dispatched, its Work "
                    "Orders, its points, its Data Health problems, daily fault hours above its siblings, and an "
                    "evidence table (this unit's key measurements vs. its siblings' median over the lookback).",
     "input_schema": _obj({"unit": {"type": "string", "description": "Unit id, e.g. VAV-S or AHU-1"}}, ["unit"])},
    {"name": "list_work_orders",
     "description": "Work Orders with their status (open, pending_approval, rejected, blocked), priority, "
                    "trade, action, and evidence.",
     "input_schema": _obj({"status": {"type": "string", "enum": ["open", "pending_approval", "rejected", "blocked"],
                                      "description": "Only this status; omit for all"}})},
    {"name": "data_health",
     "description": "Connectivity and data quality: whether rows stopped arriving, and per point coverage, "
                    "gaps, frozen values, and out-of-range readings, rolled up per unit. Use for questions "
                    "about connectivity, missing data, stuck or dead sensors, or whether the data can be trusted.",
     "input_schema": _obj({"unit": {"type": "string", "description": "Only this unit; omit for the whole site"},
                           "include_ok": {"type": "boolean", "description": "Also list healthy points (default false)"}})},
    {"name": "rule_activity",
     "description": "How much each fault rule fired, per unit: total fault hours over the data and over the "
                    "lookback, raw and above the unit's siblings (peer-adjusted), with what each rule checks. "
                    "Also lists rules that were skipped on a unit (e.g. for missing points).",
     "input_schema": _obj({"unit": {"type": "string", "description": "Only this unit"},
                           "rule": {"type": "string", "description": "Only this rule id, e.g. VAV-1"}})},
    {"name": "explain_rule",
     "description": "What a fault rule checks: its title, logic, thresholds, required point roles, and its "
                    "result on each unit.",
     "input_schema": _obj({"rule": {"type": "string", "description": "Rule id, e.g. VAV-1, FC13, SV-RATE"}}, ["rule"])},
    {"name": "find_points",
     "description": "Search points by name, unit, or Haystack tags. Returns each point's unit, tags, and "
                    "Data Health status. Use it to find the exact point names before point_data.",
     "input_schema": _obj({"text": {"type": "string", "description": "Substring of the point name (case-insensitive)"},
                           "unit": {"type": "string"},
                           "tags": {"type": "array", "items": {"type": "string"},
                                    "description": "Points carrying all of these tags, e.g. [zone, air, temp]"}})},
    {"name": "point_data",
     "description": f"Measured values for up to {MAX_POINTS} points over a time range, averaged into at most "
                    f"{MAX_ROWS} time buckets, plus min/mean/max per point. Default range: the lookback. "
                    "Use it to look at what actually happened, e.g. compare a unit's damper with a sibling's.",
     "input_schema": _obj({"points": {"type": "array", "items": {"type": "string"}, "description": "Exact point names"},
                           "start": {"type": "string", "description": "YYYY-MM-DD or YYYY-MM-DD HH:MM"},
                           "end": {"type": "string", "description": "YYYY-MM-DD or YYYY-MM-DD HH:MM (exclusive)"},
                           "operating_only": {"type": "boolean",
                                              "description": "Only times the air handler fan runs (default false)"}},
                          ["points"])},
    {"name": "request_work_order",
     "description": "Request a Work Order for a unit, ONLY when the user asks for one or agrees to your "
                    "proposal. It does not dispatch anyone: it goes into the Review Queue as pending_approval "
                    "until a person approves it in the dashboard or terminal. You cannot approve it. Priority, "
                    "trade, and action come from the remediation key, not from you. One Work Order per unit.",
     "input_schema": _obj({"unit": {"type": "string"},
                           "fault": {"type": "string", "enum": sorted(wo.load_remediation(REMEDIATION)["faults"])},
                           "reason": {"type": "string",
                                      "description": "The evidence for the technician: 2-4 sentences citing the numbers"}},
                          ["unit", "fault", "reason"])},
]


def run(site: Site, name: str, args: dict):
    fn = TOOLS.get(name)
    if fn is None:
        raise ValueError(f"no tool named {name}")
    return fn(site, **args)


# --- helpers ----------------------------------------------------------------------------

def _unit(site: Site, unit: str) -> str:
    if unit in site.units:
        return unit
    match = {u.lower(): u for u in site.units}.get(unit.lower())
    if match:
        return match
    raise ValueError(f"unknown unit {unit!r}; units are {', '.join(site.units)}")


@cache
def _rule_info(rule: str) -> dict:
    try:
        from open_fdd.rules import RULES_BY_ID
    except ImportError:
        return {}
    r = RULES_BY_ID.get(rule)
    if r is None:
        return {}
    return {"title": r.title, "summary": r.summary, "logic": r.equation, "required_roles": list(r.required_roles),
            "parameters": {p.key: {"default": p.default, "unit": p.unit, "label": p.label} for p in r.params}}


def _title(rule: str) -> str:
    return _rule_info(rule).get("title", "")


def _r(x, n=2):
    return None if x is None or pd.isna(x) else round(float(x), n)


def _wo_brief(o: dict) -> dict:
    return {k: o[k] for k in ("id", "unit", "status", "priority", "fault", "trade", "persistence", "source")}


# --- tools ------------------------------------------------------------------------------

def site_overview(site: Site) -> dict:
    m = site.meta
    orders = site.work_orders()
    units = []
    for u, x in site.units.items():
        units.append({
            "unit": u, "type": x["type"], "status": x["status"], "pattern": x["pattern"],
            "diagnosis": x["diagnosis"] and {"fault": x["diagnosis"]["fault"], "probability": x["diagnosis"]["probability"]},
            "watch_reason": x["watch_reason"],
            "work_orders": [_wo_brief(o) for o in orders if o["unit"] == u and o["status"] in ("open", "pending_approval")],
            "data_health": x["health"]["status"], "data_problems": len(x["health"]["problems"]),
        })
    return {
        "system": m["system"], "as_of": m["as_of"],
        "data": {k: m["data"][k] for k in ("start", "end", "interval_minutes", "missing_rows", "longest_row_gap_hours")},
        "lookback": f"{m['lookback_start']} to {m['as_of']} ({m['lookback_days']} days)",
        "fault_book": m["fault_book"],
        "diagnosis": {**m["diagnosis"], "signature_model": Path(m["diagnosis"]["signature_model"]).name},
        "units": units,
        "work_orders_by_status": pd.Series([o["status"] for o in orders], dtype=str).value_counts().to_dict(),
    }


def unit_details(site: Site, unit: str) -> dict:
    unit = _unit(site, unit)
    x = site.units[unit]
    start, end = site.lookback()
    con = site.cursor()
    try:
        op = site.meta.get("operating_point")
        evidence = dx.evidence_pack(con, site.cfg, unit, x["siblings"], start, end, op)
    except Exception as e:  # evidence is a bonus; never fail the whole answer for it
        evidence = [{"error": str(e)}]
    d = site.daily
    recent = d[(d.unit == unit) & (d.day >= start) & (d.day < end)]
    daily = (recent.pivot_table(index="day", columns="rule", values="excess", aggfunc="sum").fillna(0) / 60).round(2)
    hp = site.health_points
    problems = hp[(hp.unit == unit) & (hp.status != "ok")]
    return {
        "unit": unit, "type": x["type"], "fed_by": x["feeds"], "siblings": x["siblings"],
        "status": x["status"], "pattern": x["pattern"], "watch_reason": x["watch_reason"],
        "findings": [{**f, "rule_title": _title(f["rule"])} for f in x["findings"]],
        "diagnosis": x["diagnosis"],
        "work_orders": [o for o in site.work_orders() if o["unit"] == unit],
        "evidence_vs_siblings": {"period": f"{start.date()} to {(end - pd.Timedelta(days=1)).date()}",
                                 "operating_hours_only": bool(op), "rows": evidence},
        "daily_fault_hours_above_siblings": {str(i.date()): {r: v for r, v in row.items() if v}
                                             for i, row in daily.iterrows()},
        "points": {p: " ".join(t) for p, t in site.unit_points(unit).items()},
        "data_health": {"status": x["health"]["status"],
                        "problems": problems[["point", "status", "issues"]].to_dict("records")},
    }


def list_work_orders(site: Site, status: str | None = None) -> list[dict]:
    return site.work_orders(status)


def data_health(site: Site, unit: str | None = None, include_ok: bool = False) -> dict:
    hp = site.health_points
    if unit:
        unit = _unit(site, unit)
        hp = hp[hp.unit == unit]
    shown = hp if include_ok else hp[hp.status != "ok"]
    cols = [c for c in ("point", "unit", "kind", "status", "issues", "coverage", "longest_gap_hours",
                        "flat_hours", "last_change") if c in shown]
    rows = shown[cols].head(60).astype(object).where(shown[cols].head(60).notna(), None).to_dict("records")
    return {
        "file": site.meta["data"],
        "points_checked": int(len(hp)), "by_status": hp.status.value_counts().to_dict(),
        "units": {u: {"status": x["health"]["status"], "counts": x["health"]["counts"]}
                  for u, x in site.units.items() if not unit or u == unit},
        "points": rows, "points_truncated": len(shown) > 60,
        "checks": "coverage, longest gap, frozen value (temperature/humidity sensors only), plausible range; "
                  "thresholds in config/health.yaml",
    }


def rule_activity(site: Site, unit: str | None = None, rule: str | None = None) -> dict:
    d, of = site.daily, site.outcomes
    if unit:
        unit = _unit(site, unit)
        d, of = d[d.unit == unit], of[of.unit == unit]
    if rule:
        d, of = d[d.rule == rule], of[of.rule == rule]
        if of.empty:
            raise ValueError(f"rule {rule!r} didn't run here; rules: {', '.join(sorted(site.outcomes.rule.unique()))}")
    start, end = site.lookback()
    rec = d[(d.day >= start) & (d.day < end)]
    total = d.groupby(["unit", "rule"])[["minutes", "excess"]].sum() / 60
    recent = rec.groupby(["unit", "rule"])[["minutes", "excess"]].sum() / 60
    rows = []
    for (u, r), t in total.sort_values("excess", ascending=False).iterrows():
        rr = recent.loc[(u, r)] if (u, r) in recent.index else None
        rows.append({"unit": u, "rule": r, "title": _title(r), "hours_total": _r(t.minutes, 1),
                     "hours_total_above_siblings": _r(t.excess, 1),
                     "hours_lookback": _r(rr.minutes, 1) if rr is not None else 0.0,
                     "hours_lookback_above_siblings": _r(rr.excess, 1) if rr is not None else 0.0})
    skipped = of[of.status.str.startswith("SKIPPED")]
    return {"period": f"{site.meta['data']['start'][:10]} to {site.meta['as_of']}",
            "lookback": f"{start.date()} to {(end - pd.Timedelta(days=1)).date()}",
            "fired": rows[:60],
            "skipped": [{"unit": s.unit, "rule": s.rule, "status": s.status, "missing": s.missing_roles}
                        for s in skipped.itertuples()][:40]}


def explain_rule(site: Site, rule: str) -> dict:
    info = _rule_info(rule)
    of = site.outcomes[site.outcomes.rule == rule]
    if not info and of.empty:
        close = difflib.get_close_matches(rule, sorted(site.outcomes.rule.unique()), n=5)
        raise ValueError(f"unknown rule {rule!r}" + (f"; did you mean {', '.join(close)}?" if close else ""))
    return {"rule": rule, **info,
            "results": [{"unit": o.unit, "status": o.status, "fault_hours": _r(o.fault_hours, 1), "missing": o.missing_roles}
                        for o in of.itertuples()]}


def find_points(site: Site, text: str | None = None, unit: str | None = None, tags: list[str] | None = None) -> list[dict]:
    hp = site.health_points.set_index("point")
    out = []
    for name in site.columns:
        p = site.cfg.points.get(name)
        if text and text.lower() not in name.lower():
            continue
        if unit and (p is None or p.unit != _unit(site, unit)):
            continue
        if tags and (p is None or not set(tags) <= set(p.tags)):
            continue
        out.append({"point": name, "unit": p.unit if p else None, "tags": " ".join(p.tags) if p else "",
                    "excluded": p.exclude if p else "not in the points config",
                    "health": hp.status.get(name, "unknown")})
    return out[:80]


def point_data(site: Site, points: list[str], start: str | None = None, end: str | None = None,
               operating_only: bool = False) -> dict:
    if not points:
        raise ValueError("give at least one point")
    if len(points) > MAX_POINTS:
        raise ValueError(f"at most {MAX_POINTS} points at a time")
    for p in points:
        if p not in site.columns:
            close = difflib.get_close_matches(p, site.columns, n=5)
            raise ValueError(f"unknown point {p!r}" + (f"; did you mean {', '.join(close)}?" if close else ""))
    lb_start, lb_end = site.lookback()
    t0 = pd.Timestamp(start) if start else lb_start
    t1 = pd.Timestamp(end) if end else lb_end
    if t1 <= t0:
        raise ValueError("end must be after start")
    bucket = max(site.meta["data"]["interval_minutes"], -(-(t1 - t0).total_seconds() // 60 // MAX_ROWS))
    op = site.meta.get("operating_point")
    q = lambda c: '"' + c.replace('"', '""') + '"'  # noqa: E731
    where = "ts >= ? and ts < ?" + (f" and {q(op)} > 0.5" if operating_only and op else "")
    stats = site.query(f"select {', '.join(f'min({q(p)}), avg({q(p)}), max({q(p)})' for p in points)} "
                       f"from trend where {where}", [t0, t1]).iloc[0].tolist()
    series = site.query(
        f"select time_bucket(interval '{int(bucket)} minutes', ts) t, {', '.join(f'avg({q(p)}) ' + q(p) for p in points)} "
        f"from trend where {where} group by 1 order by 1", [t0, t1])
    return {
        "range": f"{t0} to {t1}", "operating_hours_only": bool(operating_only and op),
        "bucket_minutes": int(bucket),
        "stats": {p: {"min": _r(stats[3 * i]), "mean": _r(stats[3 * i + 1]), "max": _r(stats[3 * i + 2])}
                  for i, p in enumerate(points)},
        "columns": ["time"] + points,
        "rows": [[str(r.t)[:16]] + [_r(v) for v in r[1:]] for r in series.itertuples(index=False)],
    }


def request_work_order(site: Site, unit: str, fault: str, reason: str) -> dict:
    record = site.request_work_order(_unit(site, unit), fault, reason, requested_by="chat")
    return {**record, "note": "Waiting in the Review Queue: a person must approve it before it is issued."
            if record["status"] == "pending_approval" else f"Policy gate: {record['gate']} ({record['gate_rule']})"}


TOOLS = {
    "site_overview": site_overview, "unit_details": unit_details, "list_work_orders": list_work_orders,
    "data_health": data_health, "rule_activity": rule_activity, "explain_rule": explain_rule,
    "find_points": find_points, "point_data": point_data, "request_work_order": request_work_order,
}
