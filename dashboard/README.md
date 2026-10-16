# Dashboard

A Streamlit app over a **Site Snapshot** (the output of `hvac-fdd-agents analyze`), with the chat assistant built in.

```
pip install -e ".[app]"                       # streamlit
hvac-fdd-agents analyze data/sample/pfpu__damper_stuck__0pct.csv --points PFPU
hvac-fdd-agents dashboard                     # newest snapshot; or --site runs/<system>/site-...
```

The Ask view needs an Anthropic API key (sidebar, `ANTHROPIC_API_KEY`, or `.env`). Everything else works without one.

## Where things are

| File | What it draws |
|---|---|
| `app.py` | Entry point: page setup, view switcher, `--site` argument |
| `common.py` | Snapshot loading, sidebar, status labels (icon + text), Work Order cards with Approve / Reject |
| `overview.py` | KPIs, unit status table, "when each unit misbehaved" heatmap, pending and open Work Orders |
| `chat.py` | Ask: the assistant (`hvac_fdd_agents/chat/`), with suggested questions and the tools it used |
| `units.py` | One unit: findings, diagnosis ranking, evidence vs. siblings, a point vs. its siblings over time |
| `work_orders.py` | The Review Queue by status, and a form to request a Work Order |
| `data_health.py` | Connectivity and data quality: outages, per-unit rollup, problem points |
| `charts.py` | The Altair charts and the color tokens (light and dark) |

The dashboard only reads the snapshot. Its one write is a Work Order decision: a person approves or rejects (recorded with their name), or requests one by form. The assistant can request a Work Order but can't approve it.

Logic that isn't drawing lives in the package, so the terminal chat and the dashboard share it: `hvac_fdd_agents/site/` (analyze, snapshot, Work Order store), `hvac_fdd_agents/chat/` (agent and tools), `hvac_fdd_agents/data/health.py` (data health checks).
