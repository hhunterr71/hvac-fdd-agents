import json
from pathlib import Path

import pytest

from hvac_fdd_agents.claude.client import Usage
from hvac_fdd_agents.data import points_config
from hvac_fdd_agents.data.points_config import Point, PointsConfig, Unit


class FakeLLM:
    """Stands in for Claude: returns queued responses per output schema, records every call."""

    def __init__(self, responses: dict):
        self.responses = {k: list(v) for k, v in responses.items()}
        self.calls = []

    def structured(self, system, user, schema, usage: Usage):
        self.calls.append((schema.__name__, user))
        usage.calls += 1
        queue = self.responses[schema.__name__]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        return schema.model_validate(item(user) if callable(item) else item)


@pytest.fixture
def tiny_trend(tmp_path: Path) -> Path:
    """Two hours of 1-minute data: an AHU with one VAV box."""
    rows = ["Datetime,SAT,SAT_SP,FAN_STS,DMPR_1,FLOW_1,ZN_T_1,CONST_X"]
    for i in range(120):
        hh, mm = divmod(i, 60)
        on = 1 if i >= 10 else 0
        dmpr = round(0.2 + 0.6 * (i % 30) / 30, 3) if on else 0.0
        rows.append(f"01/02/2018 {hh:02d}:{mm:02d},{55 + (i % 7) * 0.1:.1f},55,{on},{dmpr},{dmpr * 800:.1f},"
                    f"{70 + (i % 11) * 0.2:.1f},42")
    path = tmp_path / "tiny.csv"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def site_dir(tmp_path: Path) -> Path:
    """A minimal Site Snapshot: a VAV box with a daily finding, a healthy one, and a smoke damper."""
    cfg = PointsConfig(
        system="TEST",
        units={"AHU-1": Unit("ahu"), "VAV-1": Unit("vav", feeds="AHU-1"), "VAV-2": Unit("vav", feeds="AHU-1"),
               "SD-1": Unit("damper")},
        points={"DMPR_1": Point("VAV-1", ["damper", "sensor"]), "DMPR_2": Point("VAV-2", ["damper", "sensor"]),
                "SMK_DMPR": Point("SD-1", ["smoke", "damper", "cmd"]), "SAT": Point("AHU-1", ["discharge", "air", "temp", "sensor"])},
    )
    points_config.write(tmp_path / "points.yaml", cfg, ["test"])
    unit = {"type": "vav", "feeds": "AHU-1", "siblings": [], "status": "ok", "pattern": "none", "findings": [],
            "diagnosis": None, "watch_reason": None, "work_order": None,
            "health": {"status": "ok", "points": 1, "counts": {}, "problems": []}}
    units = {"AHU-1": {**unit, "type": "ahu", "feeds": None},
             "VAV-1": {**unit, "siblings": ["VAV-2"], "status": "watch", "pattern": "daily",
                       "findings": [{"rule": "VAV-1", "pattern": "daily", "fault_days": 9, "hours_per_day": 9.5}]},
             "VAV-2": {**unit, "siblings": ["VAV-1"]},
             "SD-1": {**unit, "type": "damper", "feeds": None}}
    meta = {"system": "TEST", "as_of": "2018-07-15", "lookback_days": 14, "lookback_start": "2018-07-02",
            "data": {"start": "2018-07-02 00:00:00", "end": "2018-07-15 23:59:00", "interval_minutes": 1.0},
            "units": units}
    (tmp_path / "snapshot.json").write_text(json.dumps(meta), encoding="utf-8")
    (tmp_path / "work_orders.json").write_text("[]", encoding="utf-8")
    return tmp_path
