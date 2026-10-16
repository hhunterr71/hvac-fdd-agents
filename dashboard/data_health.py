"""Data Health: is the data arriving (connectivity), and can it be trusted?"""

import pandas as pd
import streamlit as st

from dashboard.common import HEALTH
from hvac_fdd_agents.site.snapshot import Site


def render(site: Site) -> None:
    f = site.meta["data"]
    st.header("Data health")
    st.caption("Rows that never arrived point to a controller or network outage; gaps on single points to one "
               "device; a frozen temperature or humidity reading to a sensor the BMS can no longer read. "
               "Thresholds: config/health.yaml.")
    c = st.columns(4)
    c[0].metric("Rows received", f"{f['rows']:,} of {f['expected_rows']:,}")
    c[1].metric("Sample interval", f"{f['interval_minutes']:g} min")
    c[2].metric("Outages (no rows at all)", f["row_gaps"])
    c[3].metric("Longest outage", f"{f['longest_row_gap_hours']:g} h")
    if f["row_gaps"]:
        st.warning(f"Longest outage started {f['longest_row_gap_start']}: no data from any point for "
                   f"{f['longest_row_gap_hours']:g} h.")

    hp = site.health_points
    st.subheader("By unit")
    rows = [{"Unit": u, "Status": HEALTH[x["health"]["status"]], "Points": x["health"]["points"],
             "OK": x["health"]["counts"].get("ok", 0), "Warning": x["health"]["counts"].get("warning", 0),
             "Bad": x["health"]["counts"].get("bad", 0)} for u, x in site.units.items()]
    unmapped = hp[hp.unit.isna()]
    if len(unmapped):
        rows.append({"Unit": "(not on a unit)", "Status": HEALTH[max(unmapped.status, key=["ok", "warning", "bad"].index)],
                     "Points": len(unmapped), "OK": int((unmapped.status == "ok").sum()),
                     "Warning": int((unmapped.status == "warning").sum()), "Bad": int((unmapped.status == "bad").sum())})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    st.subheader("Points")
    picked = st.pills("Show", ["bad", "warning", "ok"], default=["bad", "warning"], selection_mode="multi",
                      format_func=HEALTH.get, key="health-filter") or []
    shown = hp[hp.status.isin(picked)].copy()
    shown["status"] = shown.status.map(HEALTH)
    cols = ["point", "unit", "kind", "status", "issues", "coverage", "longest_gap_hours", "flat_hours", "last_change"]
    st.dataframe(shown[[c for c in cols if c in shown]], hide_index=True, width="stretch",
                 column_config={"coverage": st.column_config.NumberColumn("coverage", format="percent"),
                                "longest_gap_hours": st.column_config.NumberColumn("longest gap (h)"),
                                "flat_hours": st.column_config.NumberColumn("longest unchanged (h)")})
