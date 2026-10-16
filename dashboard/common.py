"""Shared pieces: loading a snapshot, the sidebar, status labels, Work Order cards."""

import getpass
from pathlib import Path

import streamlit as st

from hvac_fdd_agents.site.snapshot import Site, find_snapshots

# Status is shown as icon + label, never color alone.
UNIT_STATUS = {"work_order": "🔧 Work Order", "watch": "👀 Watch List", "ok": "✅ OK"}
HEALTH = {"ok": "✅ OK", "warning": "⚠️ Warning", "bad": "⛔ Bad"}
WO_STATUS = {"open": "📋 Open", "pending_approval": "⏳ Pending approval", "rejected": "✖️ Rejected",
             "blocked": "🚫 Blocked"}


@st.cache_resource(show_spinner="Loading snapshot...")
def load_site(path: str) -> Site:
    return Site(Path(path))


def fault(f: str | None) -> str:
    return (f or "").replace("_", " ")


def sidebar(cli_site: str | None) -> Site | None:
    """Snapshot picker, snapshot facts, reviewer name, API key. Returns the chosen Site."""
    sb = st.sidebar
    sb.title("HVAC FDD Agents")
    found = [str(p.resolve()) for p in find_snapshots()]
    if cli_site and str(Path(cli_site).resolve()) not in found:
        found.insert(0, str(Path(cli_site).resolve()))
    if not found:
        sb.warning("No Site Snapshot yet.")
        return None
    default = found.index(str(Path(cli_site).resolve())) if cli_site else 0
    path = sb.selectbox("Site Snapshot", found, index=default, format_func=_label)
    site = load_site(path)
    m = site.meta
    sb.caption(f"**{m['system']}** · as of **{m['as_of']}**  \n"
               f"Data {m['data']['start'][:10]} to {m['data']['end'][:10]}, every {m['data']['interval_minutes']:g} min  \n"
               f"Fault Book v{m['fault_book']['version']} · model tiebreak {'on' if m['diagnosis']['model_tiebreak'] else 'off'}")
    sb.divider()
    st.session_state.setdefault("reviewer", getpass.getuser())
    sb.text_input("Your name (recorded on approvals)", key="reviewer")
    if not api_key_ready():
        # Kept in this browser session only: never in os.environ (shared by every visitor) or on disk.
        key = sb.text_input("Anthropic API key (for Ask; this session only)", type="password")
        if key:
            st.session_state["api_key"] = key.strip()
            st.rerun()
    sb.caption("Experimental: evaluated on simulated LBNL data. Nothing here changes a building system.")
    return site


def _label(path: str) -> str:
    p = Path(path)
    return f"{p.parent.name} · {p.name}"


def api_key_ready() -> bool:
    """A key from this session's sidebar, the environment, or .env."""
    from hvac_fdd_agents.claude.credentials import ensure_api_key
    if st.session_state.get("api_key"):
        return True
    try:
        ensure_api_key(interactive=False)
        return True
    except SystemExit:
        return False


def work_order_card(site: Site, o: dict, actions: bool = True) -> None:
    """One Work Order. Pending ones get Approve / Reject (a person's decision, recorded with their name)."""
    with st.container(border=True):
        top = st.columns([3, 2])
        top[0].markdown(f"**{o['id']} · P{o['priority']} · {o['unit']}** — {fault(o['fault'])}")
        top[1].markdown(f"{WO_STATUS.get(o['status'], o['status'])} · from {o['source']}")
        st.markdown(f"**Send:** {o['trade']}  \n**Do:** {o['action']}  \n**Why:** {o['evidence']}")
        if o.get("also_observed"):
            st.caption("Also observed: " + "; ".join(o["also_observed"]))
        st.caption(f"Persistence: {o['persistence']} · ~{o['hours_per_day']} h/day · consequence: {o['consequence']} "
                   f"· policy: {o['gate_rule']} · created {o['created']}"
                   + (f" · {o['status']} by {o['decided_by']} {o['decided_at']}" if o.get("decided_by") else "")
                   + (f" · note: {o['decision_note']}" if o.get("decision_note") else ""))
        if actions and o["status"] == "pending_approval":
            note = st.text_input("Note (optional)", key=f"note-{o['id']}")
            a, r, _ = st.columns([1, 1, 4])
            if a.button("Approve", key=f"approve-{o['id']}", type="primary"):
                site.decide(o["id"], True, by=st.session_state.get("reviewer") or "unknown", note=note)
                st.rerun()
            if r.button("Reject", key=f"reject-{o['id']}"):
                site.decide(o["id"], False, by=st.session_state.get("reviewer") or "unknown", note=note)
                st.rerun()
