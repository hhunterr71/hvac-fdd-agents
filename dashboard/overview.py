"""Overview: the site at a glance."""

import pandas as pd
import streamlit as st

from dashboard import charts
from dashboard.common import HEALTH, UNIT_STATUS, WO_STATUS, fault, work_order_card
from hvac_fdd_agents.site.snapshot import Site


def row_label(site: Site, unit: str) -> str:
    return unit if site.units[unit]["siblings"] else f"{unit} (raw)"


def daily_by_unit(site: Site) -> pd.DataFrame:
    """Per unit and day: the worst rule's fault hours above siblings (at most 24), and all rules behind it."""
    d = site.daily[site.daily.excess > 0]
    if d.empty:
        return pd.DataFrame(columns=["unit", "day", "hours", "rules"])
    rules = (d.sort_values("excess", ascending=False).groupby(["unit", "day"])
             .apply(lambda g: ", ".join(f"{r} {m / 60:.1f} h" for r, m in zip(g.rule, g.excess)), include_groups=False)
             .rename("rules"))
    hours = (d.groupby(["unit", "day"]).excess.max() / 60).clip(upper=24).rename("hours")
    out = pd.concat([hours, rules], axis=1).reset_index()
    out["unit"] = out.unit.map(lambda u: row_label(site, u))
    return out


def render(site: Site) -> None:
    m = site.meta
    orders = site.work_orders()
    st.header(f"{m['system']} · as of {m['as_of']}")
    st.caption(f"Persistence lookback {m['lookback_start']} to {m['as_of']} ({m['lookback_days']} days). "
               "Everything below is from the Site Snapshot; ask the assistant to dig in.")

    units = m["units"]
    c = st.columns(5)
    c[0].metric("Units", len(units))
    c[1].metric("Open Work Orders", sum(o["status"] == "open" for o in orders))
    c[2].metric("Awaiting approval", sum(o["status"] == "pending_approval" for o in orders))
    c[3].metric("On the Watch List", sum(u["status"] == "watch" for u in units.values()))
    c[4].metric("Units with data issues", sum(u["health"]["status"] != "ok" for u in units.values()))

    active = {o["unit"]: o for o in orders if o["status"] in ("open", "pending_approval")}
    rows = []
    for unit, u in units.items():
        o = active.get(unit)
        d = u["diagnosis"]
        rows.append({
            "Unit": unit, "Type": u["type"], "Status": UNIT_STATUS[u["status"]],
            "Persistence": u["pattern"],
            "Likely problem": (f"{fault(d['fault'])} ({d['probability']:.0%})" if d else ""),
            "Work Order": f"{o['id']} P{o['priority']} · {WO_STATUS[o['status']]}" if o else "",
            "Why not dispatched": u["watch_reason"] or "",
            "Data health": HEALTH[u["health"]["status"]],
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    st.subheader("When each unit misbehaved")
    st.caption("Per day, the hours the worst rule fired on the unit beyond its worst identical sibling. "
               "Units marked (raw) have no siblings to compare with, so their noise doesn't cancel. "
               "Stronger color = more. Hover a cell for every rule behind it.")
    daily = daily_by_unit(site)
    if daily.empty:
        st.info("No rule fired on any unit beyond its siblings.")
    else:
        charts.show(charts.fault_heatmap(daily, [row_label(site, u) for u in units]))

    pending = [o for o in orders if o["status"] == "pending_approval"]
    if pending:
        st.subheader("Waiting for your approval")
        for o in pending:
            work_order_card(site, o)
    opened = [o for o in orders if o["status"] == "open"]
    if opened:
        st.subheader("Open Work Orders")
        for o in sorted(opened, key=lambda o: o["priority"]):
            work_order_card(site, o, actions=False)
