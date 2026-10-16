"""Proposed Fault Book versions waiting for a person: the Fault Book part of the Review Queue.

Each proposal is a folder in runs/faultbook/<id>/ with proposal.json (the edits, their
reasons, before/after scores on the tuning system and on the evaluation system) and
faultbook.yaml (the full proposed Fault Book). Approving one is the only way
config/faultbook.yaml changes: it becomes the next version, and the previous file is
kept next to the proposal.
"""

import json
import shutil
from datetime import datetime
from pathlib import Path

from hvac_fdd_agents import paths
from hvac_fdd_agents.rules import faultbook
from hvac_fdd_agents.rules.faultbook import FaultBook
from hvac_fdd_agents.tuning import edits as ed

DIR = paths.RUNS / "faultbook"


def save(method: str, tuned_on: str, evaluated_on: str | None, base: FaultBook, proposed: FaultBook,
         reasons: dict, tuning: dict, evaluation: dict | None, summary: str = "", extra: dict | None = None) -> Path:
    """reasons: {(kind, rule): reason}. tuning/evaluation: {"before": metrics, "after": metrics}."""
    pid = f"{datetime.now():%Y%m%d-%H%M%S}-{method}-{tuned_on}"
    folder = DIR / pid
    folder.mkdir(parents=True, exist_ok=True)
    changes = [{"kind": e.kind, "rule": e.rule, "change": e.describe().split(": ", 1)[1],
                "enabled": e.enabled, "params": e.overrides, "reason": reasons.get((e.kind, e.rule), "")}
               for e in ed.diff(base, proposed)]
    record = {"id": pid, "created": datetime.now().isoformat(timespec="seconds"), "method": method,
              "tuned_on": tuned_on, "evaluated_on": evaluated_on, "base_version": base.version,
              "summary": summary, "edits": changes, "tuning": tuning, "evaluation": evaluation,
              "status": "pending_approval", "decided_by": None, "decided_at": None, "decision_note": None,
              **(extra or {})}
    (folder / "proposal.json").write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    proposed_versioned = FaultBook(base.version + 1, base.source, proposed.rules)
    ed.write(proposed_versioned, folder / "faultbook.yaml", _header(record))
    return folder


def all_proposals() -> list[dict]:
    """Every proposal, newest first."""
    found = [json.loads(p.read_text(encoding="utf-8")) for p in DIR.glob("*/proposal.json")]
    return sorted(found, key=lambda r: r["created"], reverse=True)


def decide(pid: str, approve: bool, by: str, note: str = "") -> dict:
    """A person approves (the proposal becomes config/faultbook.yaml) or rejects it."""
    folder = DIR / pid
    record = json.loads((folder / "proposal.json").read_text(encoding="utf-8"))
    if record["status"] != "pending_approval":
        raise ValueError(f"{pid} is {record['status']}, not pending approval")
    if approve:
        live = faultbook.load(paths.FAULTBOOK)
        if live.version != record["base_version"]:
            raise ValueError(f"the Fault Book is now v{live.version}, but this proposal edits v{record['base_version']}; "
                             "re-run tuning on the current version")
        shutil.copyfile(paths.FAULTBOOK, folder / "faultbook_before.yaml")
        proposed = faultbook.load(folder / "faultbook.yaml")
        source = f"{live.source}; v{proposed.version}: {record['method']} tuning on {record['tuned_on']}, " \
                 f"approved by {by} {datetime.now():%Y-%m-%d}"
        ed.write(FaultBook(proposed.version, source, proposed.rules), paths.FAULTBOOK, _header(record))
    record.update(status="approved" if approve else "rejected", decided_by=by,
                  decided_at=datetime.now().isoformat(timespec="seconds"), decision_note=note or None)
    (folder / "proposal.json").write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    return record


def _header(record: dict) -> list[str]:
    return ["The Fault Book: which open-fdd rules run on each equipment kind, with what thresholds.",
            "Overrides go inside {}: e.g. VAV-7: {fixed_flow_min_mean: 250}. Turn a rule off with {enabled: false}.",
            "Changes are proposed by the Fault Book agent (or the plain tuner) and approved by a person",
            f"on the dashboard's Fault Book page. This version: {record['method']} tuning on {record['tuned_on']}",
            f"({record['id']})."]
