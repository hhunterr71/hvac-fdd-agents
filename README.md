# HVAC FDD Agents

> **Experimental.** A working prototype that improves over time, evaluated on public data (LBNL FDD Data Sets). Not production software; nothing here writes to a real building system.

A multi-agent facilities assistant for building HVAC data. Give it a BMS trend export and it:

1. **Tags** every point with Project Haystack tags, with a human checkpoint on equipment before point tagging.
2. **Runs fault rules** from a Fault Book seeded with [open-fdd](https://github.com/bbartling/open-fdd), which implements ASHRAE Guideline 36 fault conditions.
3. **Manages the Fault Book**: an agent finds noisy rules and coverage gaps and proposes changes for review.
4. **Diagnoses and dispatches**: flagged issues become structured Work Orders behind a policy gate.
5. **Answers questions** in a chat and a dashboard: "How is my system performing?", "How is connectivity behaving?", "Why is VAV-S flagged?". The chat can request a Work Order; a person approves it.

It is evaluated against the public [LBNL FDD Data Sets](https://faultdetection.lbl.gov/data), where every fault is labeled. Status: phases 1 (tagging), 2 (rules), and 4 (diagnose and dispatch) built and measured on the VAV development set; chat and dashboard built; phase 3 (Fault Book agent) next. See [docs/](docs/README.md) for how it works.

## Quick start

```
python -m venv .venv && .venv/Scripts/pip install -e ".[dev,fdd,app]"   # macOS/Linux: .venv/bin/pip
.venv/Scripts/hvac-fdd-agents analyze data/sample/pfpu__damper_stuck__0pct.csv --points PFPU
.venv/Scripts/hvac-fdd-agents dashboard
```

[data/sample/](data/sample/) holds two weeks of LBNL data (fault-free, and a stuck damper), so this runs without downloading anything. `analyze` and the dashboard need no API key; the chat and `tag` do.

## Analyze, chat, and the dashboard

`analyze` takes a Trend File and its approved points config and builds a **Site Snapshot** in `runs/<system>/site-<time>/`. It checks Data Health (missing rows, gaps, frozen sensors, implausible values), runs the Fault Book, builds Incidents with peer comparison and Persistence, diagnoses them with the signature model, and issues Work Orders through the policy gate ([ADR-0008](docs/decisions/0008-site-snapshot-chat-and-dashboard.md)). `--as-of YYYY-MM-DD` shows the site as it was on that day; `--tiebreak` lets the model decide close diagnoses.

- `hvac-fdd-agents chat` asks questions in the terminal. Type `/queue` to list Work Orders waiting for approval, and `/approve WO-n` or `/reject WO-n` to decide.
- `hvac-fdd-agents dashboard` opens the [Streamlit app](dashboard/README.md), which has the same chat plus Overview, Units, Work Orders, and Data health views.

The chat answers only from the snapshot through read-only tools. It cites the numbers, and it can request (never approve) a Work Order.

## Input: a Trend File

Any wide CSV with a timestamp in the first column and one column per point:

```
Datetime,RM_TEMP_S,VAV_DMPR_S,VAV_DA_CFM_S,...
01/01/2018 00:00,68.13,0.2,0.03,...
```

To use your own building, first get a points config: tag it by hand from a blank one, or let the agents propose one.

```
.venv/Scripts/hvac-fdd-agents new-config path/to/trend.csv --system MYBLDG --points-list points.csv   # blank, to tag by hand
.venv/Scripts/hvac-fdd-agents tag path/to/trend.csv --system MYBLDG                                   # agents propose it
```

`--points-list` is optional: a CSV with columns `point,desc,units`. Approved configs go in `config/points/<SYSTEM>.yaml`, and then `--points MYBLDG` finds them.

## Phase 1: tag a Trend File

On first run `tag` asks for your Anthropic API key (hidden input) and saves it to `.env`, which git ignores. It then:

1. profiles every point (range, change rate, correlated points) in DuckDB,
2. proposes equipment and pauses at the **Equipment Checkpoint** for you to approve, edit, or reject,
3. tags each unit's points in parallel with Haystack tags (or excludes them, with a reason),
4. writes `runs/<system>/<mode>-<time>/points.yaml`, `review_queue.yaml`, and `run.json` (tokens and cost).

`--mode single` runs the one-call baseline. `--reference PFPU` scores the result against `config/points/PFPU.yaml`. Tests run without an API key: `.venv/Scripts/python -m pytest`.

## LBNL benchmark

The data isn't in the repo: `python benchmark/setup/fetch.py FPU` downloads the VAV development set (~17 GB unpacked) and renames every file to its catalog name, so you get the same `data/lbnl/` the results were measured on. [benchmark/README.md](benchmark/README.md) lists every step that reproduces [docs/results/](docs/results/), in order, with its cost and run time.

## Where things are

| Path | What |
|---|---|
| `src/hvac_fdd_agents/` | The tool: the Python package behind the `hvac-fdd-agents` command. `src/` is the standard home for a package's code |
| `dashboard/` | The Streamlit app, one file per view ([README](dashboard/README.md)) |
| `config/` | Hand-written settings: `points/` (one points config per system), the Fault Book, dispatch, policy, remediation, and Data Health thresholds |
| `models/` | Learned from data (the signature model); regenerate, don't edit |
| `benchmark/` | LBNL-only: download, ground truth, and the scripts behind the results ([README](benchmark/README.md)) |
| `tests/` | `pytest`; no API key or download needed |
| `docs/` | How it works: [overview](docs/1-overview.md), [architecture](docs/2-architecture.md), [file reference](docs/3-reference.md), [glossary](docs/glossary.md), design decisions, measured results |
| `data/` | `sample/` (committed, two weeks) and `lbnl/` (downloaded, git-ignored) |
| `runs/` | Everything the tools write (git-ignored; re-created by running them) |

## Security

- **The dashboard is for this computer only.** It has no login, so it listens on `localhost`, set in `.streamlit/config.toml`. `hvac-fdd-agents dashboard --host 0.0.0.0` opens it to your network on purpose, and anyone who can reach it can then read the data, chat on your API key, and approve Work Orders. The name recorded on an approval is whatever the reviewer types, not a verified identity.
- **The API key** lives in `.env`, which git ignores, or in the environment. A key typed into the dashboard sidebar stays in that browser session and is never written to disk.
- **Agents can't act on the building.** The chat can only request a Work Order, which waits for a person's approval. Priority is computed by code, and the policy gate (`config/policy.yaml`) blocks anything that would change a setpoint or touch life-safety equipment.
- **Spend is capped:** at most 15 steps per chat message and $2 per conversation by default.
- **Trend data is treated as data.** Point names and descriptions reach the model only as tool results, and its instructions say to ignore any text in them that reads like an instruction.

## License

The code is under the [MIT License](LICENSE).

The data in `data/sample/` and the data `benchmark/setup/fetch.py` downloads are not covered by it. They are the LBNL FDD Data Sets, CC BY 4.0: Lawrence Berkeley National Laboratory, LBNL FDD Data Sets, https://faultdetection.lbl.gov/data
