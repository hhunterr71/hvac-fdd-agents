"""Fault Book: review proposed rule changes (from the Fault Book agent or the plain tuner)."""

import pandas as pd
import streamlit as st

from hvac_fdd_agents import paths
from hvac_fdd_agents.rules import faultbook
from hvac_fdd_agents.site.snapshot import Site
from hvac_fdd_agents.tuning import proposals

STATUS = {"pending_approval": "⏳ Pending approval", "approved": "✅ Approved", "rejected": "✖️ Rejected"}
METRICS = [("Faults caught", "detection_rate", "{:.0%}"),
           ("Unnecessary dispatches per 100 box-months", "unnecessary_terminal_per_100_unit_months", "{:g}"),
           ("Unnecessary AHU dispatches", "unnecessary_ahu", "{:g}"),
           ("Right fix", "right_fix", "{:.0%}"),
           ("Objective (caught − unnecessary)", "objective", "{:g}")]


def render(site: Site) -> None:
    live = faultbook.load(paths.FAULTBOOK)
    st.header("Fault Book")
    st.caption(f"Live: version {live.version} ({live.source}). Proposed changes are tuned on one system and "
               "scored on another the tuner never saw. Approving one replaces config/faultbook.yaml; "
               "re-run `analyze` to apply it to a site.")
    found = proposals.all_proposals()
    if not found:
        st.info("No proposals yet. Run `python benchmark/phase3_tune.py` to have the agent and the plain tuner "
                "propose changes.")
        return
    pid = st.selectbox("Proposal", [p["id"] for p in found],
                       format_func=lambda i: next(f"{p['method']} · tuned on {p['tuned_on']} · "
                                                  f"{STATUS[p['status']]} · {p['created'][:16]}"
                                                  for p in found if p["id"] == i))
    p = next(x for x in found if x["id"] == pid)
    st.subheader(f"{p['method'].title()} tuning: {len(p['edits'])} edit(s) to v{p['base_version']}")
    if p["summary"]:
        st.markdown(f"> {p['summary']}")

    rows = []
    for label, key, fmt in METRICS:
        row = {"Measure": label}
        for name, block in (("Tuning: before", p["tuning"]["before"]), ("Tuning: after", p["tuning"]["after"]),
                            ("Held-out: before", (p["evaluation"] or {}).get("before")),
                            ("Held-out: after", (p["evaluation"] or {}).get("after"))):
            v = block.get(key) if block else None
            row[name] = fmt.format(v) if v is not None else ""
        rows.append(row)
    st.caption(f"Tuning = {p['tuned_on']} (the tuner saw these outcomes). "
               f"Held-out = {p['evaluated_on']} (it never did); this is the result that counts.")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    st.subheader("Edits")
    if p["edits"]:
        st.dataframe(pd.DataFrame(p["edits"])[["kind", "rule", "change", "reason"]], hide_index=True, width="stretch")
    else:
        st.write("No changes: nothing the tuner tried improved the score.")
    if p.get("usage"):
        st.caption(f"Agent: {p.get('backtests_used')} backtests, {p['usage']['calls']} model calls, "
                   f"~${p['usage']['est_cost_usd']:.2f}")

    if p["status"] == "pending_approval" and p["edits"]:
        note = st.text_input("Note (optional)", key=f"fb-note-{pid}")
        a, r, _ = st.columns([1, 1, 4])
        if a.button("Approve", type="primary", key=f"fb-approve-{pid}"):
            try:
                proposals.decide(pid, True, by=st.session_state.get("reviewer") or "unknown", note=note)
                st.rerun()
            except ValueError as e:
                st.error(str(e))
        if r.button("Reject", key=f"fb-reject-{pid}"):
            proposals.decide(pid, False, by=st.session_state.get("reviewer") or "unknown", note=note)
            st.rerun()
    elif p["status"] != "pending_approval":
        st.caption(f"{STATUS[p['status']]} by {p['decided_by']} {p['decided_at']}"
                   + (f" · note: {p['decision_note']}" if p.get("decision_note") else ""))
