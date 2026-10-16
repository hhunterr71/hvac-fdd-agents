# The chat and dashboard read a Site Snapshot; the chat can request Work Orders but never approve them

The proposal planned the chat front end as a router that hands questions to the phase 1–4 agents. We built something simpler. `hvac-fdd-agents analyze` runs every deterministic step on one Trend File once (Data Health, the Fault Book, peer comparison, Persistence, diagnosis, Work Orders) and saves the results as a **Site Snapshot**: a directory of plain files, plus a Parquet copy of the trend data. The chat is one agent whose tools only read that snapshot, and the Streamlit dashboard reads the same files.

We chose this because the agents' answers already exist once `analyze` has run, so routing a question to them would only recompute them. Reading files keeps every number reproducible and lets the dashboard and the terminal chat show the same thing. The model's job in chat is the same as in phase 4: explaining the results, comparing a unit with its siblings, and reasoning about look-alike faults, never computing a status or a Priority.

The chat can **request** a Work Order, through the same policy gate as everything else. The new action `request_work_order` is `needs_human`, so a requested Work Order waits as `pending_approval` until a person approves or rejects it in the dashboard or the terminal (`/approve`). That decision is recorded with the person's name. No tool lets the model approve.

## Consequences

- A snapshot is the state as of one date (`--as-of`, default: the last day of data). Questions about other dates need a new `analyze`.
- Work Orders live in their snapshot. A new `analyze` starts a new list, so decisions don't carry over between snapshots yet.
- The shipped signature model (`models/signature_model.yaml`) is trained on all PFPU and SFPU cases. On other equipment types it only knows "none" and VAV faults (ADR-0007).
- Data Health is deterministic and separate from fault rules. Frozen-value checks apply only to temperature and humidity sensors, because positions, speeds, and flows legitimately sit still for hours.
- The model tiebreak in `analyze` (`--tiebreak`) only chooses which fault. If it answers "none", the signature model's top fault stands, because in phase 4 the tiebreak's "none" cancelled correct dispatches.
