"""Work Orders: the Review Queue (approve / reject), everything issued, and a manual request form."""

import streamlit as st

from dashboard.common import WO_STATUS, fault, work_order_card
from hvac_fdd_agents.dispatch import workorder as wo
from hvac_fdd_agents.paths import REMEDIATION
from hvac_fdd_agents.site.snapshot import Site


def render(site: Site) -> None:
    orders = site.work_orders()
    st.header("Work Orders")
    st.caption("The pipeline issues a Work Order when a persistent problem passes diagnosis and the policy gate. "
               "Ones requested in chat (or below) wait here until a person approves them.")
    counts = {s: sum(o["status"] == s for o in orders) for s in WO_STATUS}
    options = [s for s in WO_STATUS if counts[s]] or ["open"]
    default = "pending_approval" if counts["pending_approval"] else options[0]
    show = st.segmented_control("Status", options, default=default, key="wo-status",
                                format_func=lambda s: f"{WO_STATUS[s]} ({counts[s]})") or default
    chosen = sorted((o for o in orders if o["status"] == show), key=lambda o: (o["priority"], o["id"]))
    if not chosen:
        st.write("None.")
    for o in chosen:
        work_order_card(site, o)

    with st.expander("Request a Work Order"):
        faults = sorted(wo.load_remediation(REMEDIATION)["faults"])
        with st.form("request"):
            unit = st.selectbox("Unit", list(site.units))
            f = st.selectbox("Problem", faults, format_func=fault)
            reason = st.text_area("Why (the evidence for the technician)")
            if st.form_submit_button("Add to the Review Queue"):
                try:
                    o = site.request_work_order(unit, f, reason, requested_by=st.session_state.get("reviewer") or "dashboard")
                    st.success(f"{o['id']} added: {WO_STATUS[o['status']]}")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
