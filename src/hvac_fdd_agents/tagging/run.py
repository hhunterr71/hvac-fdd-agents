"""Run tagging end to end: Trend File in, points config + Review Queue + run record out."""

import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

import yaml
from langgraph.types import Command

from hvac_fdd_agents.claude.client import StructuredLLM
from hvac_fdd_agents.data import points_config, profiles, trend
from hvac_fdd_agents.data.points_config import Point, PointsConfig, Unit
from hvac_fdd_agents.tagging.graph import build_graph
from hvac_fdd_agents.tagging.single import run_single

# Called at the Equipment Checkpoint with the proposal; returns {"approved": bool, "equipment": dict | None}.
Checkpoint = Callable[[dict, list[str], Path], dict]


def auto_approve(equipment: dict, problems: list[str], proposal_file: Path) -> dict:
    return {"approved": not problems, "equipment": None}


def tag_trend_file(trend_file: Path, system: str, llm: StructuredLLM, out_dir: Path,
                   mode: str = "multi", checkpoint: Checkpoint = auto_approve) -> Path:
    started = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
    ts_col, _, ts_fmt = trend.read_header(trend_file)
    con, points = trend.load(trend_file)
    profs = profiles.compute(con, points)
    lines = {p: profs[p].line() for p in points}
    (out_dir / "profiles.txt").write_text("\n".join(lines[p] for p in points) + "\n", encoding="utf-8")
    profiled = time.time()

    waited = 0.0
    if mode == "single":
        result = run_single(llm, system, points, lines)
    else:
        graph = build_graph(llm)
        config = {"configurable": {"thread_id": str(uuid.uuid4())}}
        kinds = {p: profs[p].kind for p in points}
        state = graph.invoke({"system": system, "points": points, "profiles": lines, "kinds": kinds}, config)
        while "__interrupt__" in state:
            payload = state["__interrupt__"][0].value
            proposal_file = out_dir / "equipment_proposal.yaml"
            proposal_file.write_text(yaml.safe_dump(payload["equipment"], sort_keys=False), encoding="utf-8")
            paused = time.time()
            decision = checkpoint(payload["equipment"], payload["problems"], proposal_file)
            waited += time.time() - paused
            state = graph.invoke(Command(resume=decision), config)
        result = state

    cfg = to_config(system, points, result["equipment"], result["decisions"], result["review"])
    header = [f"Points config for {system}, proposed by the tagging agents ({mode}-agent mode).",
              f"Generated {datetime.now():%Y-%m-%d %H:%M} from {Path(trend_file).name}. Review before use;",
              "points marked review: need a human decision."]
    points_config.write(out_dir / "points.yaml", cfg, header, {"column": ts_col, "format": ts_fmt})
    (out_dir / "review_queue.yaml").write_text(
        yaml.safe_dump(result["review"], sort_keys=False, allow_unicode=True), encoding="utf-8")
    (out_dir / "equipment.yaml").write_text(
        yaml.safe_dump(result["equipment"], sort_keys=False, allow_unicode=True), encoding="utf-8")
    record = {
        "system": system, "trend_file": str(trend_file), "mode": mode,
        "model": getattr(llm, "model", "fake"), "effort": getattr(llm, "effort", None),
        "points": len(points), "units": len(result["equipment"]["units"]),
        "review_items": len(result["review"]),
        "seconds": {"profiling": round(profiled - started, 1),
                    "waiting_for_human": round(waited, 1),
                    "agents": round(time.time() - profiled - waited, 1)},
        "usage": result["usage"], "total_cost_usd": round(sum(u["est_cost_usd"] for u in result["usage"]), 4),
    }
    (out_dir / "run.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return out_dir / "points.yaml"


def to_config(system: str, points: list[str], equipment: dict, decisions: list[dict],
              review: list[dict]) -> PointsConfig:
    units = {u["id"]: Unit(type=u["type"], feeds=u.get("served_by")) for u in equipment["units"]}
    by_point = {d["point"]: d for d in decisions}
    reasons = {}
    for r in review:
        reasons.setdefault(r["point"], []).append(r["reason"])
    cfg_points = {}
    for p in points:
        d = by_point.get(p)
        review_note = "; ".join(reasons.get(p, [])) or None
        if d is None:
            cfg_points[p] = Point(exclude="no decision from agent", review=review_note or "no decision")
        elif d["action"] == "exclude":
            cfg_points[p] = Point(unit=d["unit"], exclude=d["exclude_reason"], review=review_note)
        else:
            cfg_points[p] = Point(unit=d["unit"], tags=d["tags"], review=review_note)
    return PointsConfig(system=system, units=units, points=cfg_points)
