import duckdb
import numpy as np
import pandas as pd

from hvac_fdd_agents.data import health
from hvac_fdd_agents.data.points_config import Point, PointsConfig, Unit

HCFG = health.HealthConfig(ranges=((frozenset({"temp"}), -40, 250),))


def trend_con():
    """12 h of 1-minute data with one hour of rows missing, and one point per kind of problem."""
    ts = pd.date_range("2018-07-02", periods=720, freq="min")
    wobble = 70 + np.sin(np.arange(720) / 30)
    df = pd.DataFrame({
        "ts": ts,
        "GOOD_T": wobble,
        "FROZEN_T": np.where((ts >= "2018-07-02 02:00") & (ts < "2018-07-02 09:00"), 71.234, wobble),
        "CONST_T": 120.0,
        "DMPR": 0.0,                                                    # damper closed: legitimately flat
        "GARBAGE_T": np.where(np.arange(720) % 20 == 0, -9999.0, wobble),
        "BLANK_T": np.where((ts >= "2018-07-02 06:00") & (ts < "2018-07-02 08:00"), np.nan, wobble),
    })
    df = df[(df.ts < "2018-07-02 10:00") | (df.ts >= "2018-07-02 11:00")]   # controller offline for an hour
    con = duckdb.connect()
    con.register("df", df)
    con.execute("create table trend as select * from df")
    return con


CFG = PointsConfig("T", {"U": Unit("vav")}, {
    p: Point("U", ["zone", "air", "temp", "sensor"]) for p in ("GOOD_T", "FROZEN_T", "CONST_T", "GARBAGE_T", "BLANK_T")
} | {"DMPR": Point("U", ["damper", "sensor"])})


def test_file_check_finds_the_outage():
    f = health.check_file(trend_con())
    assert (f["rows"], f["expected_rows"], f["missing_rows"], f["row_gaps"]) == (660, 720, 60, 1)
    assert f["longest_row_gap_hours"] == 1.0 and f["longest_row_gap_start"].startswith("2018-07-02 10:00")


def test_each_problem_gets_the_right_status():
    con = trend_con()
    df = health.check_points(con, CFG, HCFG, health.check_file(con)).set_index("point")
    assert df.loc["GOOD_T", "status"] == "warning"           # only the shared 1 h outage
    assert df.loc["FROZEN_T", "status"] == "warning" and "stuck at 71.234 for 7" in df.loc["FROZEN_T", "issues"]
    assert "never changes" in df.loc["CONST_T", "issues"]
    assert df.loc["DMPR", "issues"] == "only 92% of expected samples; no data for 1 h at a stretch"  # flat at 0: fine
    assert df.loc["GARBAGE_T", "status"] == "bad" and "outside the plausible range" in df.loc["GARBAGE_T", "issues"]
    assert df.loc["BLANK_T", "status"] == "bad" and "only 75% of expected samples" in df.loc["BLANK_T", "issues"]


def test_rollup_takes_the_worst_point():
    con = trend_con()
    by = health.by_unit(health.check_points(con, CFG, HCFG, health.check_file(con)))
    assert by["U"]["status"] == "bad" and by["U"]["counts"]["bad"] == 2
