import pandas as pd

from hvac_fdd_agents.dispatch import diagnose as dx
from hvac_fdd_agents.dispatch import incidents as inc
from hvac_fdd_agents.dispatch import policy as pol
from hvac_fdd_agents.dispatch import workorder as wo

CFG = inc.DispatchConfig()
AS_OF = pd.Timestamp("2018-03-31")


def days(n_days, minutes, end=AS_OF, every=1):
    idx = [end - pd.Timedelta(days=i) for i in range(0, n_days, every)]
    return pd.Series(float(minutes), index=pd.DatetimeIndex(idx))


def test_windows_split_at_midnight():
    w = pd.DataFrame([{"unit": "V1", "rule": "R", "start": pd.Timestamp("2018-01-01 23:30"),
                       "end": pd.Timestamp("2018-01-02 00:29")}])
    d = inc.daily_minutes(w).set_index("day").minutes
    assert d[pd.Timestamp("2018-01-01")] == 30 and d[pd.Timestamp("2018-01-02")] == 30


def test_peer_adjustment_cancels_noise_shared_by_all_siblings():
    rows = [{"unit": u, "rule": "VAV-1", "day": AS_OF, "minutes": m} for u, m in
            [("V1", 600), ("V2", 600), ("V3", 600), ("V4", 900)]]
    peers = {u: [p for p in ["V1", "V2", "V3", "V4"] if p != u] for u in ["V1", "V2", "V3", "V4"]}
    out = inc.peer_adjust(pd.DataFrame(rows), peers, "median")
    assert out.set_index("unit").excess.to_dict() == {"V4": 300}


def test_worst_sibling_baseline_only_flags_the_box_that_stands_out_from_all():
    rows = [{"unit": u, "rule": "VAV-1", "day": AS_OF, "minutes": m} for u, m in
            [("V1", 300), ("V2", 600), ("V3", 700), ("V4", 1000)]]      # zones naturally differ
    peers = {u: [p for p in ["V1", "V2", "V3", "V4"] if p != u] for u in ["V1", "V2", "V3", "V4"]}
    assert inc.peer_adjust(pd.DataFrame(rows), peers, "median").unit.tolist() == ["V3", "V4"]
    assert inc.peer_adjust(pd.DataFrame(rows), peers, "worst").set_index("unit").excess.to_dict() == {"V4": 300}


def test_persistence_patterns():
    op = 14 * 600
    assert inc.classify(days(14, 500), AS_OF, op, CFG) == "continuous"
    assert inc.classify(days(8, 90), AS_OF, op, CFG) == "daily"
    assert inc.classify(days(14, 90, every=7), AS_OF, op, CFG) == "weekly"       # every Monday
    assert inc.classify(days(1, 90), AS_OF, op, CFG) == "sporadic"
    old = days(2, 90, end=AS_OF - pd.Timedelta(days=10))
    assert inc.classify(old, AS_OF, op, CFG) == "stale"                         # nothing in the last 9 days
    assert inc.classify(days(5, 30), AS_OF, op, CFG) is None                    # never reaches 60 min/day


def test_weekly_problem_does_not_go_stale_between_occurrences():
    # Judged the day before the next Monday: last occurrences were 6 and 13 days ago.
    mondays = pd.Series([90.0, 90.0], index=pd.DatetimeIndex([AS_OF - pd.Timedelta(days=6),
                                                              AS_OF - pd.Timedelta(days=13)]))
    assert inc.classify(mondays, AS_OF, 8400, CFG) == "weekly"


def test_signature_model_learns_faults_and_noise():
    ex = ([(frozenset({"VAV-4", "VAV-1"}), "damper_stuck")] * 5
          + [(frozenset({"VAV-REHEAT"}), "reheat_valve_stuck")] * 5
          + [(frozenset({"SV-RATE"}), dx.NONE)] * 10)
    m = dx.SignatureModel.fit(ex)
    assert m.rank(frozenset({"VAV-4", "VAV-1"}))[0][0] == "damper_stuck"
    assert m.rank(frozenset({"SV-RATE"}))[0][0] == dx.NONE


def test_model_is_only_asked_on_close_calls():
    m = dx.SignatureModel.fit([(frozenset({"A"}), "f1")] * 9 + [(frozenset({"B"}), "f2")] * 9)
    asked = []
    tb = lambda ranking: asked.append(ranking) or dx.TiebreakAnswer(fault="f2", confidence="high", explanation="x")
    assert dx.diagnose(m, frozenset({"A"}), 0.2, tb).fault == "f1" and not asked      # clear case
    assert dx.diagnose(m, frozenset({"A", "B"}), 0.2, tb).method == "signature+model" and asked


REMEDIATION = {"faults": {"damper_stuck": {"trade": "controls technician", "action": "fix actuator",
                                            "consequence": "comfort"}},
               "priority_base": {"safety": 1, "equipment": 2, "energy": 3, "comfort": 3}}
POLICY = {"rules": [{"name": "life safety", "when": {"target_tags_any": ["fire", "smoke"]}, "decision": "block"},
                    {"name": "wo", "when": {"action": "create_work_order"}, "decision": "approve"},
                    {"name": "sp", "when": {"action": "change_setpoint", "within_bounds": True}, "decision": "needs_human"}],
          "default": "block"}


def test_one_work_order_per_unit_with_priority_from_the_worst_finding():
    incident = inc.Incident("VAV-S", AS_OF, [inc.Finding("VAV-1", "daily", 10, 9.0),
                                             inc.Finding("SV-FLATLINE", "sporadic", 2, 0.5)])
    d = dx.Diagnosis("damper_stuck", 0.8, [("damper_stuck", 0.8), ("none", 0.2)], "signature")
    o = wo.build(incident, d, CFG, REMEDIATION, POLICY)
    assert o.priority == 2                        # comfort (3), but 9 h/day bumps it up
    assert o.also_observed == ["SV-FLATLINE (sporadic)"]
    assert o.gate == "approve" and "WORK ORDER  P2  VAV-S" in o.render()


def test_none_diagnosis_or_sporadic_incident_means_no_work_order():
    sporadic = inc.Incident("VAV-S", AS_OF, [inc.Finding("VAV-1", "sporadic", 1, 0.2)])
    d = dx.Diagnosis("damper_stuck", 0.9, [], "signature")
    assert wo.build(sporadic, d, CFG, REMEDIATION, POLICY) is None
    daily = inc.Incident("VAV-S", AS_OF, [inc.Finding("VAV-1", "daily", 9, 2.0)])
    assert wo.build(daily, dx.Diagnosis(dx.NONE, 0.9, [], "signature"), CFG, REMEDIATION, POLICY) is None


def test_policy_gate_never_lets_safety_actions_through():
    adversarial = [pol.Action("create_work_order", target_tags=["smoke", "damper"]),
                   pol.Action("change_setpoint", target_tags=["fire"], within_bounds=True),
                   pol.Action("disable_alarm"), pol.Action("override_point", within_bounds=True),
                   pol.Action("something_new")]
    assert all(pol.evaluate(POLICY, a).decision == "block" for a in adversarial)
    assert pol.evaluate(POLICY, pol.Action("change_setpoint", within_bounds=True)).decision == "needs_human"


def test_none_threshold_sets_how_cautious_dispatch_is():
    m = dx.SignatureModel.fit([(frozenset({"A"}), "f1")] * 4 + [(frozenset({"A"}), dx.NONE)] * 6)
    assert dx.diagnose(m, frozenset({"A"}), 0.2, none_threshold=0.5).fault == dx.NONE    # P(none) ~0.6
    assert dx.diagnose(m, frozenset({"A"}), 0.2, none_threshold=0.7).fault == "f1"


def test_signature_model_round_trips_through_yaml(tmp_path):
    m = dx.SignatureModel.fit([(frozenset({"A"}), "f1")] * 3 + [(frozenset({"B"}), dx.NONE)] * 5)
    m.save(tmp_path / "m.yaml", ["test"])
    back = dx.SignatureModel.load(tmp_path / "m.yaml")
    assert back.rules == m.rules and back.rank(frozenset({"A"}))[0][0] == "f1"
    assert abs(back.rank(frozenset({"B"}))[0][1] - m.rank(frozenset({"B"}))[0][1]) < 1e-4


def test_operating_minutes_scale_with_the_sample_interval():
    import duckdb

    from hvac_fdd_agents.data.points_config import Point, PointsConfig, Unit
    cfg = PointsConfig("T", {"AHU-1": Unit("ahu")}, {"FAN": Point("AHU-1", ["discharge", "fan", "run", "sensor"])})
    con = duckdb.connect()
    con.execute("create table trend as select ts, (hour(ts) between 6 and 17)::int as FAN "
                "from range(timestamp '2018-07-02', timestamp '2018-07-03', interval 15 minute) t(ts)")
    op, point = inc.operating_minutes(con, cfg)
    assert point == "FAN" and op.iloc[0] == 12 * 60                 # 48 on-samples x 15 min
