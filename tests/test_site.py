import pytest

from hvac_fdd_agents.site.snapshot import Site


def test_requested_work_order_waits_for_a_person(site_dir):
    site = Site(site_dir)
    o = site.request_work_order("VAV-1", "damper_stuck", "Damper flat at 0 while siblings move.")
    assert o["status"] == "pending_approval" and o["source"] == "chat" and o["id"] == "WO-001"
    assert o["priority"] == 2                     # comfort (3), bumped for 9.5 h/day; computed, not chosen
    assert o["trade"] == "controls technician" and o["also_observed"] == ["VAV-1 (daily)"]
    assert site.work_orders("pending_approval") == [o]


def test_one_active_work_order_per_unit(site_dir):
    site = Site(site_dir)
    site.request_work_order("VAV-1", "damper_stuck", "x")
    with pytest.raises(ValueError, match="already has WO-001"):
        site.request_work_order("VAV-1", "airflow_sensor_bias", "y")
    site.decide("WO-001", False, by="hunter")
    assert site.request_work_order("VAV-1", "airflow_sensor_bias", "y")["id"] == "WO-002"   # rejected frees it


def test_only_pending_work_orders_can_be_decided(site_dir):
    site = Site(site_dir)
    site.request_work_order("VAV-1", "damper_stuck", "x")
    o = site.decide("WO-001", True, by="hunter", note="go")
    assert (o["status"], o["decided_by"], o["decision_note"]) == ("open", "hunter", "go")
    assert Site(site_dir).work_orders("open")[0]["id"] == "WO-001"                         # persisted
    with pytest.raises(ValueError, match="not pending"):
        site.decide("WO-001", False, by="hunter")
    with pytest.raises(ValueError, match="no Work Order"):
        site.decide("WO-999", True, by="hunter")


def test_bad_requests_are_refused_with_a_reason(site_dir):
    site = Site(site_dir)
    with pytest.raises(ValueError, match="unknown unit"):
        site.request_work_order("VAV-9", "damper_stuck", "x")
    with pytest.raises(ValueError, match="unknown fault"):
        site.request_work_order("VAV-1", "gremlins", "x")
    with pytest.raises(ValueError, match="reason"):
        site.request_work_order("VAV-1", "damper_stuck", "  ")


def test_life_safety_equipment_is_blocked_by_the_gate(site_dir):
    o = Site(site_dir).request_work_order("SD-1", "damper_stuck", "x")
    assert o["status"] == "blocked" and o["gate_rule"] == "never touch life-safety equipment"
