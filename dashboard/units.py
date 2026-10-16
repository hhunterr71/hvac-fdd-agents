"""Units: one unit in depth, compared with its identical siblings."""

import math

import pandas as pd
import streamlit as st

from dashboard import charts
from dashboard.common import HEALTH, UNIT_STATUS, work_order_card
from hvac_fdd_agents.chat import tools
from hvac_fdd_agents.rules.roles import best_match
from hvac_fdd_agents.site.snapshot import Site

MAX_ROWS = 600


@st.cache_data(show_spinner=False)
def _details(path: str, unit: str, _site: Site) -> dict:
    return tools.unit_details(_site, unit)


def render(site: Site) -> None:
    units = list(site.units)
    first = next((u for u in units if site.units[u]["status"] == "work_order"), units[0])
    unit = st.selectbox("Unit", units, index=units.index(first),
                        format_func=lambda u: f"{u} · {UNIT_STATUS[site.units[u]['status']]}")
    u = site.units[unit]
    d = _details(str(site.path), unit, site)

    st.header(unit)
    st.caption(f"{u['type']}" + (f" · fed by {u['feeds']}" if u["feeds"] else "")
               + (f" · identical siblings: {', '.join(u['siblings'])}" if u["siblings"] else " · no identical siblings")
               + f" · data health {HEALTH[u['health']['status']]}")
    if u["watch_reason"]:
        st.info(f"{UNIT_STATUS[u['status']]}: {u['watch_reason']}")

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Rule findings over the lookback")
        if u["findings"]:
            f = pd.DataFrame(d["findings"]).rename(columns={
                "rule": "Rule", "rule_title": "What it checks", "pattern": "Persistence",
                "fault_days": "Fault days", "hours_per_day": "h/day above siblings"})
            st.dataframe(f[["Rule", "What it checks", "Persistence", "Fault days", "h/day above siblings"]],
                         hide_index=True, width="stretch")
        else:
            st.write("No rule fired beyond its siblings.")
    with right:
        st.subheader("Likely problem")
        dg = u["diagnosis"]
        if dg:
            charts.show(charts.ranking_bars(dg["ranking"]))
            st.caption(f"Signature model{' + model tiebreak' if dg['method'] != 'signature' else ''}; "
                       f"it dispatches only if P(none) is below {site.meta['diagnosis']['none_threshold']:.0%}.")
            if dg["explanation"]:
                st.markdown(f"> {dg['explanation']}")
        else:
            st.write("Not diagnosed: nothing persistent enough.")

    st.subheader("This unit vs. its siblings")
    ev = [r for r in d["evidence_vs_siblings"]["rows"] if "error" not in r]
    if ev:
        st.caption(f"Means over {d['evidence_vs_siblings']['period']}"
                   + (" during operating hours" if d["evidence_vs_siblings"]["operating_hours_only"] else ""))
        st.dataframe(pd.DataFrame(ev).rename(columns={"measure": "Measure", "unit": unit,
                                                      "siblings_median": "Siblings' median"}),
                     hide_index=True, width="stretch")
    compare(site, unit)

    orders = [o for o in site.work_orders() if o["unit"] == unit]
    if orders:
        st.subheader("Work Orders")
        for o in orders:
            work_order_card(site, o)
    problems = d["data_health"]["problems"]
    if problems:
        st.subheader("Data health problems")
        st.dataframe(pd.DataFrame(problems).assign(status=lambda x: x.status.map(HEALTH)),
                     hide_index=True, width="stretch")


def compare(site: Site, unit: str) -> None:
    points = site.unit_points(unit)
    if not points:
        return
    names = list(points)
    default = next((p for p in names if {"damper", "sensor"} <= set(points[p])),
                   next((p for p in names if {"zone", "temp", "sensor"} <= set(points[p])), names[0]))
    a, b = st.columns([2, 1])
    point = a.selectbox("Point", names, index=names.index(default),
                        format_func=lambda p: f"{p} · {' '.join(points[p])}")
    span = b.segmented_control("Range", ["Lookback", "All data"], default="Lookback", key=f"span-{unit}") or "Lookback"
    tags = points[point]
    series = {point: point}
    for sib in site.units[unit]["siblings"]:
        p = best_match(site.cfg, sib, tags)
        if p:
            series[p] = p
    if span == "Lookback":
        t0, t1 = site.lookback()
    else:
        t0 = pd.Timestamp(site.meta["data"]["start"])
        t1 = pd.Timestamp(site.meta["data"]["end"]) + pd.Timedelta(minutes=1)
    step = site.meta["data"]["interval_minutes"]
    bucket = max(step, math.ceil((t1 - t0).total_seconds() / 60 / MAX_ROWS))
    q = lambda c: '"' + c.replace('"', '""') + '"'  # noqa: E731
    df = site.query(f"select time_bucket(interval '{int(bucket)} minutes', ts) t, "
                    + ", ".join(f"avg({q(p)}) as {q(p)}" for p in series)
                    + " from trend where ts >= ? and ts < ? group by 1 order by 1", [t0, t1])
    long = df.melt(id_vars="t", var_name="series", value_name="value").dropna()
    charts.show(charts.compare_lines(long, point, " ".join(tags)))
    st.caption(f"{bucket:g}-minute averages. Gray: the same point on "
               + (", ".join(p for p in series if p != point) or "no sibling") + ". Hover for values.")
