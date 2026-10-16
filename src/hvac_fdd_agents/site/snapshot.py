"""Read a Site Snapshot, and keep its Work Orders (the Work Order part of the Review Queue).

Work Order statuses:
  open              issued (the pipeline's, when the policy gate approves them)
  pending_approval  waiting for a person; every Work Order requested in chat starts here
  rejected          a person said no
  blocked           the policy gate refused it (e.g. life-safety equipment)
Only `decide` (a person, from the dashboard or the terminal) moves a Work Order out of
pending_approval. The chat can request one but never approve it.
"""

import json
import threading
from dataclasses import asdict
from datetime import datetime
from functools import cached_property
from pathlib import Path

import duckdb
import pandas as pd

from hvac_fdd_agents import paths
from hvac_fdd_agents.data import points_config
from hvac_fdd_agents.dispatch import policy as pol
from hvac_fdd_agents.dispatch import workorder as wo

GATE_STATUS = {"approve": "open", "needs_human": "pending_approval", "block": "blocked"}
ACTIVE = ("open", "pending_approval")


def order_record(order: wo.WorkOrder, n: int, source: str, requested_by: str | None = None) -> dict:
    return {"id": f"WO-{n:03d}", **asdict(order), "source": source, "status": GATE_STATUS[order.gate],
            "created": datetime.now().isoformat(timespec="seconds"), "requested_by": requested_by,
            "decided_by": None, "decided_at": None, "decision_note": None}


def find_snapshots(runs: Path = paths.RUNS) -> list[Path]:
    """Snapshot directories under runs/, newest first."""
    found = [p.parent for p in Path(runs).glob("*/site-*/snapshot.json")]
    return sorted(found, key=lambda p: (p / "snapshot.json").stat().st_mtime, reverse=True)


class Site:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.meta = json.loads((self.path / "snapshot.json").read_text(encoding="utf-8"))
        self.cfg = points_config.load(self.path / "points.yaml")
        self._con = None
        self._lock = threading.Lock()

    @property
    def system(self) -> str:
        return self.meta["system"]

    @property
    def units(self) -> dict[str, dict]:
        return self.meta["units"]

    # --- results tables -------------------------------------------------------------

    @cached_property
    def health_points(self) -> pd.DataFrame:
        return pd.read_csv(self.path / "health_points.csv")

    @cached_property
    def windows(self) -> pd.DataFrame:
        return pd.read_csv(self.path / "candidate_windows.csv", parse_dates=["start", "end"])

    @cached_property
    def outcomes(self) -> pd.DataFrame:
        return pd.read_csv(self.path / "rule_outcomes.csv")

    @cached_property
    def daily(self) -> pd.DataFrame:
        return pd.read_csv(self.path / "daily_rule_minutes.csv", parse_dates=["day"])

    def cursor(self):
        """A DuckDB cursor with table `trend` (the Trend File). One per caller, so threads don't collide."""
        with self._lock:
            if self._con is None:
                self._con = duckdb.connect()
                path = str((self.path / "trend.parquet").resolve()).replace("\\", "/").replace("'", "''")
                self._con.execute(f"create view trend as select * from read_parquet('{path}')")
            return self._con.cursor()

    def query(self, sql: str, params: list | None = None) -> pd.DataFrame:
        return self.cursor().execute(sql, params or []).df()

    @cached_property
    def columns(self) -> list[str]:
        return [c for c in self.query("select column_name from (describe trend)").column_name if c != "ts"]

    def lookback(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        """The Persistence lookback the snapshot was judged over: [start, end)."""
        end = pd.Timestamp(self.meta["as_of"]) + pd.Timedelta(days=1)
        return end - pd.Timedelta(days=self.meta["lookback_days"]), end

    def unit_points(self, unit: str) -> dict[str, list[str]]:
        return {p: x.tags for p, x in self.cfg.points.items() if x.unit == unit and x.kept}

    # --- Work Orders ----------------------------------------------------------------

    def work_orders(self, status: str | None = None) -> list[dict]:
        orders = json.loads((self.path / "work_orders.json").read_text(encoding="utf-8"))
        return [o for o in orders if status is None or o["status"] == status]

    def _save(self, orders: list[dict]) -> None:
        tmp = self.path / "work_orders.json.tmp"
        tmp.write_text(json.dumps(orders, indent=2), encoding="utf-8")
        tmp.replace(self.path / "work_orders.json")

    def request_work_order(self, unit: str, fault: str, reason: str, requested_by: str = "chat") -> dict:
        """Add a Work Order a person asked for. It goes through the policy gate; by default it
        waits in pending_approval. Raises ValueError with a reason the chat can relay."""
        if unit not in self.units:
            raise ValueError(f"unknown unit {unit!r}; units are {', '.join(self.units)}")
        remediation = wo.load_remediation(paths.REMEDIATION)
        if fault not in remediation["faults"]:
            raise ValueError(f"unknown fault {fault!r}; choose one of {', '.join(remediation['faults'])}")
        if not reason.strip():
            raise ValueError("a reason (the evidence) is required")
        with self._lock:
            orders = self.work_orders()
            existing = [o for o in orders if o["unit"] == unit and o["status"] in ACTIVE]
            if existing:
                o = existing[0]
                raise ValueError(f"{unit} already has {o['id']} ({o['status']}, {o['fault']}); "
                                 f"one Work Order per unit (ADR-0006)")
            u = self.units[unit]
            live = [f for f in u["findings"] if f["pattern"] != "stale"]
            persistence = u["pattern"] if u["pattern"] != "none" else "not detected by rules"
            hours = max((f["hours_per_day"] for f in live), default=0.0)
            fix = remediation["faults"][fault]
            tags = sorted({t for ts in self.unit_points(unit).values() for t in ts})
            gate = pol.evaluate(pol.load(paths.POLICY), pol.Action("request_work_order", target_tags=tags))
            order = wo.WorkOrder(
                unit=unit, as_of=self.meta["as_of"],
                priority=wo.priority(fix["consequence"], persistence, hours, remediation),
                fault=fault, trade=fix["trade"], action=fix["action"], consequence=fix["consequence"],
                persistence=persistence, hours_per_day=hours, evidence=reason.strip(),
                also_observed=[f"{f['rule']} ({f['pattern']})" for f in live],
                gate=gate.decision, gate_rule=gate.rule, method="requested in chat",
            )
            n = max((int(o["id"].split("-")[1]) for o in orders), default=0) + 1
            record = order_record(order, n, source="chat", requested_by=requested_by)
            self._save(orders + [record])
        return record

    def decide(self, order_id: str, approve: bool, by: str, note: str = "") -> dict:
        """A person approves or rejects a pending Work Order."""
        with self._lock:
            orders = self.work_orders()
            match = [o for o in orders if o["id"] == order_id]
            if not match:
                raise ValueError(f"no Work Order {order_id}")
            o = match[0]
            if o["status"] != "pending_approval":
                raise ValueError(f"{order_id} is {o['status']}, not pending approval")
            o.update(status="open" if approve else "rejected", decided_by=by,
                     decided_at=datetime.now().isoformat(timespec="seconds"), decision_note=note or None)
            self._save(orders)
        return o
