"""HVAC FDD Agents dashboard. Run: hvac-fdd-agents dashboard [--site runs/<system>/site-...]
(or: streamlit run dashboard/app.py -- --site ...). See dashboard/README.md."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # so `dashboard.*` imports work under streamlit

import streamlit as st  # noqa: E402

from dashboard import chat, common, data_health, overview, units, work_orders  # noqa: E402

st.set_page_config(page_title="HVAC FDD Agents", page_icon="🌡️", layout="wide")

VIEWS = {"Overview": overview, "Ask": chat, "Units": units, "Work Orders": work_orders, "Data health": data_health}


def cli_site() -> str | None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site")
    return ap.parse_known_args(sys.argv[1:])[0].site


site = common.sidebar(cli_site())
if site is None:
    st.title("HVAC FDD Agents")
    st.info("No Site Snapshot found under runs/. Build one first, e.g.:\n\n"
            "`hvac-fdd-agents analyze data/sample/pfpu__damper_stuck__0pct.csv --points PFPU`")
    st.stop()

pending = len(site.work_orders("pending_approval"))
view = st.segmented_control(
    "View", list(VIEWS), default="Overview", key="view", label_visibility="collapsed",
    format_func=lambda v: f"{v} ({pending})" if v == "Work Orders" and pending else v) or "Overview"
VIEWS[view].render(site)
