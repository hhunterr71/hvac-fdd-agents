import pandas as pd
import pytest

from hvac_fdd_agents.data.points_config import PointsConfig, Unit
from hvac_fdd_agents.dispatch import diagnose as dx
from hvac_fdd_agents.dispatch import incidents as inc
from hvac_fdd_agents.rules import faultbook
from hvac_fdd_agents.rules.faultbook import FaultBook
from hvac_fdd_agents.tuning import edits as ed
from hvac_fdd_agents.tuning import proposals
from hvac_fdd_agents.tuning.agent import Session
from hvac_fdd_agents.tuning.backtest import Backtester, Result, Row, score

BOOK = FaultBook(1, "test", {"vav": {"VAV-1": {}, "VAV-7": {}}, "ahu": {"FC1": {}}})
REMEDIATION = {"faults": {"damper_stuck": {"trade": "controls technician", "action": "fix actuator",
                                            "consequence": "comfort"}},
               "priority_base": {"safety": 1, "equipment": 2, "energy": 3, "comfort": 3}}
POLICY = {"rules": [{"name": "wo", "when": {"action": "create_work_order"}, "decision": "approve"}], "default": "block"}


def test_edits_are_checked_against_open_fdd_bounds():
    assert ed.validate(BOOK, ed.make("vav", "VAV-1", params={"zone_hi": 78})) == []
    assert "outside 72..85" in ed.validate(BOOK, ed.make("vav", "VAV-1", params={"zone_hi": 99}))[0]
    assert "no parameter" in ed.validate(BOOK, ed.make("vav", "VAV-1", params={"made_up": 1}))[0]
    assert "isn't a rule for vav" in ed.validate(BOOK, ed.make("vav", "FC1"))[0]
    with pytest.raises(ValueError):
        ed.apply(BOOK, [ed.make("vav", "VAV-1", params={"zone_hi": 99})])


def test_apply_diff_and_write_round_trip(tmp_path):
    new = ed.apply(BOOK, [ed.make("vav", "VAV-1", params={"zone_hi": 78}), ed.make("vav", "VAV-7", enabled=False)])
    assert "VAV-7" not in new.enabled("vav") and new.enabled("vav")["VAV-1"] == {"zone_hi": 78}
    assert [e.describe() for e in ed.diff(BOOK, new)] == ["vav/VAV-1: zone_hi=78", "vav/VAV-7: disable"]
    assert BOOK.rules["vav"]["VAV-1"] == {}                                  # the original is untouched
    ed.write(new, tmp_path / "fb.yaml", ["test"])
    assert faultbook.load(tmp_path / "fb.yaml").rules == new.rules


def test_dispatches_close_with_work_order_outcomes():
    cfg = PointsConfig("T", {"AHU-1": Unit("ahu"), "V1": Unit("vav", "AHU-1"), "V2": Unit("vav", "AHU-1")}, {})
    bt = Backtester(None, cfg, [], inc.DispatchConfig(), REMEDIATION, POLICY)
    as_of = pd.Timestamp("2018-07-31")
    finding = [inc.Finding("VAV-1", "daily", 10, 4.0)]
    rows = [Row("c1", "V1", as_of, inc.Incident("V1", as_of, finding), "damper_stuck", "V1"),   # faulty, flagged
            Row("c1", "V2", as_of, inc.Incident("V2", as_of, finding), dx.NONE, "V1"),          # healthy, flagged
            Row("c1", "AHU-1", as_of, inc.Incident("AHU-1", as_of), dx.NONE, "V1")]
    model = dx.SignatureModel.fit([(frozenset({"VAV-1"}), "damper_stuck")] * 9 + [(frozenset(), dx.NONE)] * 9)
    ds = bt.dispatch(rows, model)
    assert [(d.row.unit, d.outcome) for d in ds] == [("V1", "confirmed"), ("V2", "no_fault_found")]
    m = score(rows, ds, REMEDIATION, bt.ahus)
    assert m["detection_rate"] == 1.0 and m["unnecessary_terminal_per_100_unit_months"] == 100.0
    assert m["outcomes"] == {"confirmed": 1, "wrong_diagnosis": 0, "no_fault_found": 1} and m["objective"] == 0.0


class FakeBacktester:
    """Scores a book by how many rules it disables: each disabled rule is +10."""

    def __init__(self):
        self.runs = 0

    def run(self, book, model=None, log=None):
        self.runs += 1
        off = sum(1 for k in book.rules for p in book.rules[k].values() if p.get("enabled") is False)
        metrics = {"objective": 10.0 * off, "detection_rate": 0.5, "unnecessary_terminal_per_100_unit_months": 10.0,
                   "unnecessary_ahu": 0, "right_fix": 0.5, "outcomes": {}}
        return Result(metrics, [], [], None, [])


def test_agent_session_enforces_bounds_backtest_before_keep_and_budget():
    s = Session(FakeBacktester(), BOOK, budget=3, log=lambda m: None)
    assert s.used == 1                                                       # the starting backtest
    with pytest.raises(ValueError, match="outside"):
        s.backtest([{"kind": "vav", "rule": "VAV-1", "enabled": True, "params": {"zone_hi": 99}, "reason": "x"}])
    assert s.used == 1                                                       # rejected edits cost nothing
    with pytest.raises(ValueError, match="no backtest 7"):
        s.keep(7)                                                            # can't keep what wasn't measured
    r = s.backtest([{"kind": "vav", "rule": "VAV-7", "enabled": False, "reason": "noisy"}])
    assert r["objective_change"] == 10.0 and s.book is BOOK                  # backtest alone changes nothing
    s.keep(r["backtest_id"])
    assert "VAV-7" not in s.book.enabled("vav") and s.kept[0].reason == "noisy"
    s.backtest([{"kind": "ahu", "rule": "FC1", "enabled": False, "reason": "x"}])
    with pytest.raises(ValueError, match="budget"):
        s.backtest([{"kind": "vav", "rule": "VAV-1", "enabled": False, "reason": "x"}])


def test_keeping_a_stale_backtest_is_refused():
    s = Session(FakeBacktester(), BOOK, budget=5, log=lambda m: None)
    a = s.backtest([{"kind": "vav", "rule": "VAV-7", "enabled": False, "reason": "x"}])["backtest_id"]
    b = s.backtest([{"kind": "ahu", "rule": "FC1", "enabled": False, "reason": "x"}])["backtest_id"]
    s.keep(a)
    with pytest.raises(ValueError, match="changed since"):
        s.keep(b)                                                            # measured on the old book


def test_approving_a_proposal_writes_the_next_version(tmp_path, monkeypatch):
    live = tmp_path / "faultbook.yaml"
    ed.write(BOOK, live, ["test"])
    monkeypatch.setattr(proposals.paths, "FAULTBOOK", live)
    monkeypatch.setattr(proposals, "DIR", tmp_path / "proposals")
    tuned = ed.apply(BOOK, [ed.make("vav", "VAV-7", enabled=False)])
    m = {"objective": 1}
    folder = proposals.save("agent", "PFPU", "SFPU", BOOK, tuned, {("vav", "VAV-7"): "noisy"},
                            {"before": m, "after": m}, {"before": m, "after": m})
    p = proposals.all_proposals()[0]
    assert p["status"] == "pending_approval" and p["edits"][0]["reason"] == "noisy"
    proposals.decide(folder.name, True, by="hunter")
    new = faultbook.load(live)
    assert new.version == 2 and "VAV-7" not in new.enabled("vav") and "approved by hunter" in new.source
    with pytest.raises(ValueError, match="not pending"):
        proposals.decide(folder.name, False, by="hunter")


def test_a_proposal_for_an_old_version_cannot_be_approved(tmp_path, monkeypatch):
    live = tmp_path / "faultbook.yaml"
    ed.write(FaultBook(3, "test", BOOK.rules), live, ["test"])
    monkeypatch.setattr(proposals.paths, "FAULTBOOK", live)
    monkeypatch.setattr(proposals, "DIR", tmp_path / "proposals")
    m = {"objective": 1}
    folder = proposals.save("plain", "PFPU", "SFPU", BOOK, BOOK, {}, {"before": m, "after": m}, None)
    with pytest.raises(ValueError, match="now v3"):
        proposals.decide(folder.name, True, by="hunter")
