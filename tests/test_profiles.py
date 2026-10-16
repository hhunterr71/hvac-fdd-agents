import pytest

from hvac_fdd_agents.data import profiles, trend


def test_detects_both_lbnl_timestamp_formats():
    assert trend.detect_timestamp_format("01/01/2018 00:00") == "%m/%d/%Y %H:%M"
    assert trend.detect_timestamp_format("2018-01-01 01:00:00") == "%Y-%m-%d %H:%M:%S"
    with pytest.raises(ValueError):
        trend.detect_timestamp_format("not a time")


def test_profiles(tiny_trend):
    con, points = trend.load(tiny_trend)
    assert points == ["SAT", "SAT_SP", "FAN_STS", "DMPR_1", "FLOW_1", "ZN_T_1", "CONST_X"]
    prof = profiles.compute(con, points)

    assert prof["CONST_X"].constant
    assert prof["CONST_X"].line() == "CONST_X: constant 42"
    assert prof["FAN_STS"].values == [0.0, 1.0]
    assert prof["DMPR_1"].min == 0.0 and prof["DMPR_1"].max <= 0.8
    assert prof["SAT"].change_rate > 0.5
    # Two hours is too short for correlations to mean anything, so none are reported.
    assert all(p.correlated == [] for p in prof.values())
