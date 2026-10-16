"""Ask: chat with the site's assistant. It reads the snapshot through tools and can request
(never approve) Work Orders; approvals happen on the Work Orders page."""

import anthropic
import streamlit as st

from dashboard.common import api_key_ready
from hvac_fdd_agents.chat.agent import Chat
from hvac_fdd_agents.site.snapshot import Site

SUGGESTIONS = [
    "How is my system performing?",
    "How is connectivity behaving?",
    "Which unit should we fix first, and why?",
    "Is anything on the Watch List likely to become a real problem?",
]
TOOL_LABELS = {
    "site_overview": "site overview", "unit_details": "unit details", "list_work_orders": "Work Orders",
    "data_health": "data health", "rule_activity": "rule activity", "explain_rule": "rule definition",
    "find_points": "point search", "point_data": "trend data", "request_work_order": "Work Order request",
}


def render(site: Site) -> None:
    st.header("Ask about this site")
    if not api_key_ready():
        st.warning("The assistant needs an Anthropic API key: add it in the sidebar, set ANTHROPIC_API_KEY, or run "
                   "`hvac-fdd-agents chat` once in a terminal to save it to .env.")
        return
    state = st.session_state.setdefault(f"chat::{site.path}", {"chat": None, "history": []})
    if state["chat"] is None:
        key = st.session_state.get("api_key")   # sidebar key: this session only
        state["chat"] = Chat(site, client=anthropic.Anthropic(api_key=key) if key else None)
    chat: Chat = state["chat"]
    if state["history"] and st.button("New conversation"):
        state.update(chat=None, history=[])
        st.rerun()

    for h in state["history"]:
        with st.chat_message(h["role"]):
            if h.get("tools"):
                st.caption("Looked up: " + ", ".join(h["tools"]))
            st.markdown(h["text"])

    picked = None
    if not state["history"]:
        st.caption("Answers come from the Site Snapshot as of " + site.meta["as_of"] + ". Try:")
        picked = st.pills("Suggestions", SUGGESTIONS, label_visibility="collapsed", key=f"suggest::{site.path}")
    prompt = st.chat_input("Ask about units, faults, data, or Work Orders...") or picked
    if not prompt:
        if chat.usage.calls:
            st.caption(f"This conversation: {chat.usage.calls} model calls, ~${chat.usage.cost_usd:.2f}")
        return

    state["history"].append({"role": "user", "text": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        status = st.status("Thinking...", expanded=False)
        used: list[str] = []

        def stream():
            for ev in chat.send(prompt):
                if ev.kind == "text":
                    yield ev.text
                elif ev.kind == "tool_call":
                    label = TOOL_LABELS.get(ev.name, ev.name)
                    used.append(label)
                    status.update(label=f"Looking up {label}...")
                    status.write(f"{ev.name}({', '.join(f'{k}={v!r}' for k, v in ev.data.items())})")
                elif ev.kind == "tool_result" and ev.data.get("is_error"):
                    status.write(f"↳ {ev.text}")
                elif ev.kind == "notice":
                    yield f"\n\n*{ev.text}*"

        text = st.write_stream(stream())
        status.update(label=("Looked up: " + ", ".join(used)) if used else "Answered", state="complete")
    state["history"].append({"role": "assistant", "text": text if isinstance(text, str) else "".join(map(str, text)),
                             "tools": used})
    if "Work Order request" in used:
        st.rerun()   # refresh counts and the Review Queue
