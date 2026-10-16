"""Analyze a site: a Trend File and its approved points config -> a Site Snapshot.

Runs every deterministic step once, as of the last day in the Trend File, and saves the
results so the chat and the dashboard only read files:

  data health -> Fault Book (Candidate Windows) -> peer comparison -> Incidents
  -> diagnosis (signature model; the model tiebreak only if asked) -> Work Orders

Output directory (runs/<system>/site-<time>/):
  snapshot.json            period, units and their status, findings, diagnoses, counts
  work_orders.json         Work Orders and their status (the Review Queue for Work Orders)
  health_points.csv        Data Health per point
  candidate_windows.csv    every flagged window
  rule_outcomes.csv        each rule's result per unit (fault hours, or skipped and why)
  daily_rule_minutes.csv   fault minutes per unit, rule, and day; raw and above siblings
  points.yaml              copy of the points config used
  trend.parquet            the Trend File, for fast loading by the chat and the dashboard
"""

import json
import shutil
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pandas as pd

from hvac_fdd_agents.claude.client import Usage
from hvac_fdd_agents.data import health, points_config, trend
from hvac_fdd_agents.dispatch import diagnose as dx
from hvac_fdd_agents.dispatch import incidents as inc
from hvac_fdd_agents.dispatch import policy as pol
from hvac_fdd_agents.dispatch import workorder as wo
from hvac_fdd_agents.paths import PIPELINE
from hvac_fdd_agents.rules import engine, faultbook, roles
from hvac_fdd_agents.site.snapshot import order_record


def analyze(trend_file: Path, points_file: Path, out: Path, config: dict | None = None,
            llm=None, as_of: str | None = None, log=print) -> Path:
    """Build a Site Snapshot in `out`. `config` overrides files in paths.PIPELINE (e.g. {"faultbook": ...}).
    `llm` (optional) enables the model tiebreak on close calls. `as_of` (YYYY-MM-DD) ignores
    data after that day, to see the site as it was then; default is the last day in the file."""
    paths = {k: Path(v) for k, v in {**PIPELINE, **(config or {})}.items()}
    trend_file, points_file, out = Path(trend_file), Path(points_file), Path(out)
    cfg = points_config.load(points_file)
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(points_file, out / "points.yaml")

    log(f"Loading {trend_file.name}")
    con, _ = trend.load(trend_file)
    if as_of:
        con.execute("delete from trend where ts >= ?::date + interval 1 day", [as_of])
        if not con.execute("select count(*) from trend").fetchone()[0]:
            raise ValueError(f"no data on or before {as_of}")
    con.execute(f"copy trend to '{_sql_path(out / 'trend.parquet')}' (format parquet)")

    log("Checking data health")
    file_health = health.check_file(con)
    hp = health.check_points(con, cfg, health.load_config(paths["health"]), file_health)
    hp.to_csv(out / "health_points.csv", index=False)
    unit_health = health.by_unit(hp)

    log("Running the Fault Book")
    book = faultbook.load(paths["faultbook"])
    windows, outcomes = engine.run(trend_file, cfg, book, roles.load_role_map(paths["roles"]), con=con)
    wf, of = engine.to_frames(windows, outcomes)
    wf.to_csv(out / "candidate_windows.csv", index=False)
    of.to_csv(out / "rule_outcomes.csv", index=False)

    log("Building Incidents")
    dcfg = inc.load_config(paths["dispatch"])
    peers = inc.peers_for(cfg)
    operating, op_point = inc.operating_minutes(con, cfg)
    daily = inc.daily_minutes(wf)
    excess = inc.peer_adjust(daily, peers, dcfg.peer_baseline)
    daily.merge(excess, on=["unit", "rule", "day"], how="left").fillna({"excess": 0.0}) \
        .to_csv(out / "daily_rule_minutes.csv", index=False)
    as_of = pd.Timestamp(file_health["end"]).normalize()
    incidents = inc.incidents_as_of(excess, as_of, list(cfg.units), operating, dcfg)

    log("Diagnosing and building Work Orders")
    model = dx.SignatureModel.load(paths["signature_model"])
    remediation = wo.load_remediation(paths["remediation"])
    policy = pol.load(paths["policy"])
    usage = Usage()
    end = as_of + pd.Timedelta(days=1)
    start = end - pd.Timedelta(days=dcfg.lookback_days)
    units, orders = {}, []
    for unit, incident in incidents.items():
        diagnosis, order = None, None
        if incident.dispatchable(dcfg):
            tb = None
            if llm is not None:
                tb = _tiebreaker(llm, usage, con, cfg, unit, peers, incident, dcfg, start, end, op_point)
            diagnosis = dx.diagnose(model, incident.signature(dcfg), dx.MARGIN, tb, dcfg.none_threshold)
            order = wo.build(incident, diagnosis, dcfg, remediation, policy)
        if order is not None:
            orders.append(order_record(order, len(orders) + 1, source="pipeline"))
        live = [f for f in incident.findings if f.pattern != "stale"]
        units[unit] = {
            "type": cfg.units[unit].type, "feeds": cfg.units[unit].feeds, "siblings": peers.get(unit, []),
            "status": "work_order" if order else ("watch" if live else "ok"),
            "pattern": incident.pattern(dcfg),
            "findings": [asdict(f) for f in incident.findings],
            "diagnosis": None if diagnosis is None else {
                "fault": diagnosis.fault, "probability": round(diagnosis.probability, 3),
                "method": diagnosis.method, "explanation": diagnosis.explanation,
                "ranking": [[f, round(p, 3)] for f, p in diagnosis.ranking[:5]]},
            "watch_reason": _watch_reason(incident, diagnosis, dcfg) if live and not order else None,
            "work_order": orders[-1]["id"] if order else None,
            "health": unit_health.get(unit, {"status": "ok", "points": 0, "counts": {}, "problems": []}),
        }

    snapshot = {
        "system": cfg.system,
        "created": datetime.now().isoformat(timespec="seconds"),
        "trend_file": str(trend_file.resolve()),
        "points_file": str(points_file.resolve()),
        "as_of": str(as_of.date()),
        "lookback_days": dcfg.lookback_days,
        "lookback_start": str(start.date()),
        "data": file_health,
        "operating_point": op_point,
        "fault_book": {"version": book.version, "source": book.source},
        "diagnosis": {"signature_model": str(paths["signature_model"]), "model_tiebreak": llm is not None,
                      "none_threshold": dcfg.none_threshold},
        "units": units,
        "counts": {
            "units": len(units),
            "work_orders": len(orders),
            "watch": sum(u["status"] == "watch" for u in units.values()),
            "health": {s: sum(u["health"]["status"] == s for u in units.values()) for s in health.STATUS_ORDER},
            "candidate_windows": int(len(wf)),
        },
        "model_usage": usage.as_dict(),
    }
    (out / "snapshot.json").write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    (out / "work_orders.json").write_text(json.dumps(orders, indent=2), encoding="utf-8")
    return out


def _tiebreaker(llm, usage, con, cfg, unit, peers, incident, dcfg, start, end, op_point):
    def decide(ranking):
        evidence = dx.evidence_pack(con, cfg, unit, peers.get(unit, []), start, end, op_point)
        answer = dx.tiebreak(llm, unit, incident.persistent(dcfg), ranking, evidence, usage)
        if answer.fault == dx.NONE:
            # The model only chooses *which* fault; whether to dispatch stays with the
            # deterministic steps (phase 4: its "none" cancelled correct dispatches).
            answer.fault, answer.explanation = ranking[0][0], ""
        return answer
    return decide


def _watch_reason(incident, diagnosis, dcfg) -> str:
    if diagnosis is not None and diagnosis.fault == dx.NONE:
        return (f"persistent, but its rule pattern looks like noise to the signature model "
                f"(P(none) = {diagnosis.probability:.0%})")
    if incident.dispatchable(dcfg):
        return "persistent, but no remediation is defined for the diagnosed fault"
    return f"{incident.pattern(dcfg)}: not persistent enough to send someone (ADR-0006)"


def _sql_path(p: Path) -> str:
    return str(p.resolve()).replace("\\", "/").replace("'", "''")
