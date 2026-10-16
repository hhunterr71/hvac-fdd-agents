import pandas as pd
import pytest

from hvac_fdd_agents.data.points_config import Point, PointsConfig, Unit
from hvac_fdd_agents.rules import engine, faultbook, roles
from hvac_fdd_agents.scoring.detection import score

ROLE_MAP = {
    "vav": {"damper": [{"tags": ["damper", "sensor"]}, {"tags": ["damper", "cmd"]}],
            "zone-airflow": [{"tags": ["air", "flow", "sensor"]}],
            "outside-air-temp": [{"tags": ["outside", "air", "temp", "sensor"], "from": "served_by"}]},
    "ahu": {"discharge-air-temp": [{"tags": ["discharge", "air", "temp", "sensor"]}]},
}


def tiny_config() -> PointsConfig:
    return PointsConfig(
        system="tiny",
        units={"AHU-1": Unit("ahu"), "VAV-1": Unit("vav", feeds="AHU-1")},
        points={
            "SAT": Point("AHU-1", ["discharge", "air", "temp", "sensor"]),
            "OAT": Point("AHU-1", ["outside", "air", "temp", "sensor"]),
            "DMPR_1": Point("VAV-1", ["damper", "sensor"]),
            "FLOW_1": Point("VAV-1", ["air", "flow", "sensor"]),
            "DA_FLOW_1": Point("VAV-1", ["discharge", "air", "flow", "sensor"]),
            "CONST_X": Point("VAV-1", exclude="constant"),
        },
    )


def test_roles_use_most_specific_match_and_the_feeding_unit():
    mapping = roles.resolve(tiny_config(), "VAV-1", ROLE_MAP)
    assert mapping == {"damper": "DMPR_1", "zone-airflow": "FLOW_1", "outside-air-temp": "OAT"}


def test_ambiguous_tags_resolve_to_nothing():
    cfg = tiny_config()
    cfg.points["DMPR_2"] = Point("VAV-1", ["damper", "sensor"])
    assert "damper" not in roles.resolve(cfg, "VAV-1", ROLE_MAP)


def test_faultbook_overrides_and_disables(tmp_path):
    path = tmp_path / "fb.yaml"
    path.write_text("version: 2\nsource: test\nequipment:\n  vav:\n"
                    "    VAV-4: {}\n    VAV-7: {fixed_flow_min_mean: 250}\n    VAV-1: {enabled: false}\n")
    book = faultbook.load(path)
    assert book.enabled("vav") == {"VAV-4": {}, "VAV-7": {"fixed_flow_min_mean": 250}}
    assert book.enabled("ahu") == {}


def test_confirmed_fault_runs_become_candidate_windows():
    idx = pd.date_range("2018-01-01", periods=10, freq="min")
    mask = pd.Series([0, 1, 1, 1, 0, 0, 1, 1, 0, 0], index=idx).astype(bool)
    w = engine._windows(mask, "VAV-1", "VAV-4")
    assert [(x.start.minute, x.end.minute, x.minutes) for x in w] == [(1, 3, 3.0), (6, 7, 2.0)]


def test_detection_counts_only_hours_beyond_the_twin():
    outcomes = pd.DataFrame([
        # twin: VAV-1 comfort rule is noisy all year
        {"case": "ff.csv", "unit": "VAV-1", "rule": "VAV-1", "fault_hours": 3000},
        {"case": "ff.csv", "unit": "VAV-2", "rule": "VAV-1", "fault_hours": 3000},
        # faulted case: VAV-4 responds on the faulted box; VAV-1 noise unchanged
        {"case": "stuck.csv", "unit": "VAV-1", "rule": "VAV-1", "fault_hours": 3010},
        {"case": "stuck.csv", "unit": "VAV-1", "rule": "VAV-4", "fault_hours": 900},
        {"case": "stuck.csv", "unit": "VAV-2", "rule": "VAV-1", "fault_hours": 3000},
    ])
    catalog = pd.DataFrame([
        {"system": "X", "file": "ff.csv", "fault_free": "True", "unit": "", "component": "", "mode": "", "severity": ""},
        {"system": "X", "file": "stuck.csv", "fault_free": "False", "unit": "VAV-1", "component": "damper",
         "mode": "stuck", "severity": "100pct"},
    ])
    s = score(outcomes, catalog, "X")
    row = s.cases.iloc[0]
    assert row.detected and row.rules == "VAV-4" and not row.noisy_healthy
    assert s.coverage.loc["damper_stuck", "VAV-4"] == 1.0
    assert s.background.loc["VAV-1"].sum() == pytest.approx(6000)
