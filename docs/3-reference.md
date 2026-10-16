# 3. File reference

Every folder and file: what it does, what's in it, and what uses it. For how the pieces connect, see [2-architecture.md](2-architecture.md).

## Root

| File | What it is |
|---|---|
| `README.md` | Quick start, commands, security notes |
| `pyproject.toml` | The project's settings for pip, pytest, and ruff: dependencies, the optional extras (`dev`, `fdd`, `app`), the `hvac-fdd-agents` command, and lint rules |
| `.gitignore` | Keeps `.env`, downloaded data, `runs/`, and caches out of git (`data/sample/` is the one data folder committed) |
| `.env` | Your API key (`ANTHROPIC_API_KEY=...`). Created on first use; never committed |
| `.streamlit/config.toml` | Dashboard server settings: listen on this computer only, no usage statistics |

## `src/hvac_fdd_agents/`: the tool

The Python package the `hvac-fdd-agents` command runs. `src/` is the standard home for a package's code; `pip install -e .` makes it importable and links it to the files here, so edits take effect immediately.

### Top level

| File | What it does | Key names |
|---|---|---|
| `cli.py` | Every command. Each `cmd_*` function is one subcommand; `main()` defines their arguments | `new-config`, `tag`, `score-tagging`, `analyze`, `chat`, `dashboard`; `interactive_checkpoint` (the Equipment Checkpoint prompt) |
| `paths.py` | Where every file and folder is. Code imports locations from here instead of building paths | `ROOT`, `CONFIG`, `POINTS`, `RUNS`, `LBNL`, `PIPELINE` (the files `analyze` reads), `points_config(system)` |
| `__main__.py` | Lets `python -m hvac_fdd_agents` work like the command | |

### `data/`: the input

| File | What it does | Key names | Used by |
|---|---|---|---|
| `trend.py` | Loads a Trend File into DuckDB as table `trend` with a `ts` timestamp column; detects the timestamp format; quotes point names for SQL | `load`, `read_header`, `quote` | almost everything |
| `points_config.py` | Reads and writes points configs (units, and each point's unit, tags, or exclusion reason). Also writes a blank one for hand tagging | `PointsConfig`, `Unit`, `Point`, `load`, `write`, `write_blank`, `read_points_list` | tagging, rules, analyze, scoring |
| `profiles.py` | **Point Profiles**: one line of statistics per point (range, typical values, change rate, stepped or continuous, correlated points), computed in DuckDB | `compute`, `fmt`, `PointProfile` | tagging |
| `haystack.py` | The Haystack tag vocabulary and equipment types agents may use, and a validator. Agents propose tags; this decides if they're legal | `validate_tags`, `vocabulary_text`, `EQUIPMENT_TYPES` | tagging prompts and validation |
| `health.py` | **Data Health**: missing rows (`check_file`), and per point coverage, longest gap, frozen readings (temperature/humidity only), out-of-range share (`check_point`), status and plain-language issues (`judge`), per-unit rollup (`by_unit`) | `check_file`, `check_points`, `by_unit`, `load_config` | analyze |

### `claude/`: talking to the model

| File | What it does | Key names |
|---|---|---|
| `client.py` | The one structured-output call used by tagging and the diagnosis tiebreak: caches the system prompt, falls back to another model on a refusal, tracks tokens and cost | `LLM.structured`, `Usage` (tokens and $), `StructuredLLM` (the interface tests fake), `DEFAULT_MODEL` |
| `credentials.py` | Finds the API key: environment, then `.env`, then (first run, in a terminal) a hidden prompt that saves it to `.env` after making sure git ignores it | `ensure_api_key` |

The chat streams with tools, so it calls the SDK directly in `chat/agent.py`, using the same model and fallback settings.

### `tagging/`: phase 1

| File | What it does | Key names |
|---|---|---|
| `graph.py` | The LangGraph workflow: Equipment Agent → Equipment Checkpoint (interrupt) → one Point Tagger per group of identical units, in parallel → finalize. Copies a group's decisions to its siblings | `build_graph`, `finalize`, `apply_to_siblings` |
| `prompts.py` | **Every tagging prompt.** System prompts for the Equipment Agent, Point Tagger, and single-agent baseline, plus the user-message builders | `EQUIPMENT_SYSTEM`, `TAGGER_SYSTEM`, `SINGLE_AGENT_SYSTEM`, `CONVENTIONS` |
| `schemas.py` | The exact shape each agent must return (Pydantic), enforced by structured outputs | `EquipmentProposal`, `UnitTagging`, `PointDecision`, `SingleAgentOutput` |
| `validate.py` | Code checks on agent output: every point assigned once, legal types and tags, valid feeds. Failures trigger one retry, then go to the Review Queue | `check_equipment`, `check_decisions` |
| `siblings.py` | Finds identical units by stripping the unit token from point names (`VAV_DMPR_S` → `VAV_DMPR_{u}`) and comparing profiles | `groups`, `generic_names` |
| `single.py` | The baseline: one call that does grouping and tagging at once, scored the same way | `run_single` |
| `run.py` | Runs tagging end to end and writes the outputs (below) | `tag_trend_file`, `auto_approve` |

A tagging run writes `runs/<system>/<mode>-<time>/`: `profiles.txt` (what the agents saw), `equipment_proposal.yaml` and `equipment.yaml` (before and after the checkpoint), `points.yaml` (the proposed config), `review_queue.yaml`, `run.json` (tokens, cost, timing), and `score.json` (when `--reference` is given).

### `rules/`: phase 2

| File | What it does | Key names |
|---|---|---|
| `faultbook.py` | Loads the Fault Book: which open-fdd rules run per equipment kind, with threshold overrides. `seed` is the one-off that wrote `config/faultbook.yaml` v1 from open-fdd's catalog; nothing calls it now | `FaultBook`, `load`, `seed` |
| `roles.py` | Finds each rule's points by Haystack tags (from `config/haystack_roles.yaml`), including points on the feeding AHU. Ambiguous matches resolve to nothing rather than a guess | `resolve`, `best_match` |
| `engine.py` | Runs the enabled rules on each unit through open-fdd and turns confirmed fault runs into Candidate Windows | `run`, `to_frames`, `CandidateWindow`, `RuleOutcome` |

### `dispatch/`: phase 4

| File | What it does | Key names |
|---|---|---|
| `incidents.py` | Daily fault minutes per unit and rule; peer comparison against identical siblings; Persistence over the lookback; one Incident per unit. Also each unit's siblings and the AHU's daily operating minutes | `daily_minutes`, `peer_adjust`, `classify`, `incidents_as_of`, `peers_for`, `operating_minutes`, `DispatchConfig` |
| `diagnose.py` | The **Fault Signature** model (naive Bayes over which rules persist, with a "none" class), the evidence table (this unit vs. siblings' median), and the **tiebreak agent** with its prompt | `SignatureModel` (`fit`, `rank`, `save`, `load`), `diagnose`, `evidence_pack`, `tiebreak`, `TIEBREAK_SYSTEM`, `MARGIN` |
| `workorder.py` | Builds a Work Order from an Incident and a diagnosis: the remediation lookup, computed Priority, "also observed" notes, and the policy gate result | `WorkOrder`, `build`, `priority`, `load_remediation` |
| `policy.py` | The policy gate: evaluates an action against `config/policy.yaml`; first matching rule wins, unknown actions are blocked | `evaluate`, `Action`, `Decision` |

### `site/`: one site's current state

| File | What it does | Key names |
|---|---|---|
| `analyze.py` | Runs data health, rules, Incidents, diagnosis, and Work Orders on one Trend File and writes a Site Snapshot (layout in [2-architecture.md](2-architecture.md#what-a-site-snapshot-holds)) | `analyze` |
| `snapshot.py` | Reads a snapshot (`Site`): its metadata, tables, and SQL over its data. Keeps its Work Orders: `request_work_order` (through the policy gate, usually ending pending approval) and `decide` (a person approves or rejects) | `Site`, `find_snapshots`, `order_record` |

### `chat/`: the assistant

| File | What it does | Key names |
|---|---|---|
| `agent.py` | **The site assistant and its prompt.** Runs the tool loop with streaming, yields events (text, tool calls, results, notices) for the terminal and the dashboard, and enforces 15 steps per message and a $2 cap per conversation | `Chat.send`, `Event`, `SYSTEM` |
| `tools.py` | The nine tools: their descriptions and input schemas (what the model sees) and implementations (what runs). All read the snapshot except `request_work_order` | `SCHEMAS`, `run`, `site_overview`, `unit_details`, `list_work_orders`, `data_health`, `rule_activity`, `explain_rule`, `find_points`, `point_data`, `request_work_order` |

### `scoring/`: benchmark only

| File | What it does |
|---|---|
| `tagging.py` | Scores a proposed points config against the Reference Mapping: equipment grouping, keep/exclude selection, and tags (including "rule-findable": would a rule find this point?) |
| `detection.py` | Scores rule outcomes per case: does a rule respond on the faulted unit beyond the fault-free twin, on healthy units, on the AHU? |

## `dashboard/`: the Streamlit app

One file per view. See [dashboard/README.md](../dashboard/README.md).

| File | What it draws |
|---|---|
| `app.py` | Entry point: page setup, the view switcher, the `--site` argument |
| `common.py` | Snapshot loading (cached), the sidebar (snapshot picker, your name, session-only API key), status labels, Work Order cards with Approve / Reject |
| `overview.py` | KPIs, the unit status table, the "when each unit misbehaved" heatmap, pending and open Work Orders |
| `chat.py` | Ask: the assistant, suggested questions, which tools it used, a New conversation button |
| `units.py` | One unit: findings, diagnosis ranking, evidence vs. siblings, a chosen point vs. its siblings over time |
| `work_orders.py` | The Review Queue by status, and a form to request a Work Order |
| `data_health.py` | Outages, per-unit rollup, the problem points |
| `charts.py` | The Altair charts and their light/dark color tokens |

## `config/`: hand-written settings

| File | Controls | Read by |
|---|---|---|
| `points/<SYSTEM>.yaml` | One points config per system: units, and each point's unit and tags (or why it's excluded). The 8 LBNL ones are the Reference Mappings | `analyze`, scoring |
| `faultbook.yaml` | Which fault rules run per equipment kind, and threshold overrides | `rules/` |
| `haystack_roles.yaml` | Which Haystack tags fill each open-fdd point role | `rules/roles.py` |
| `dispatch.yaml` | Lookback, peer comparison, Persistence thresholds, and the "none" threshold (how cautious dispatch is) | `dispatch/incidents.py` |
| `remediation.yaml` | Per fault: trade, action, consequence; base Priority per consequence. Drafted, not field-reviewed | `dispatch/workorder.py` |
| `policy.yaml` | The policy gate's rules | `dispatch/policy.py` |
| `health.yaml` | Data Health thresholds, which sensors are checked for frozen values, and plausible ranges | `data/health.py` |

## `models/`

| File | What it is |
|---|---|
| `signature_model.yaml` | The trained Fault Signature model: P(rule persists given fault) and priors, for 10 classes over 22 rules. Regenerate with `benchmark/train_signature_model.py`; don't edit |

## `benchmark/`: LBNL evaluation

Step-by-step order, costs, and run times are in [benchmark/README.md](../benchmark/README.md).

| File | What it does |
|---|---|
| `lbnl.py` | Shared helpers: load the case catalog, rename downloaded files |
| `phase2_run_rules.py` | Runs the Fault Book on every case of a system in parallel; writes Candidate Windows and rule outcomes to `runs/fdd/` |
| `phase2_score_rules.py` | Scores those outcomes against the labels (→ `results/phase2-rules-baseline.md`) |
| `phase4_dispatch.py` | Builds Incidents at 12 month-ends per case and scores each dispatch variant: persistence only, + peer comparison, + signature model (→ `results/phase4-dispatch-dev.md`) |
| `phase4_tiebreak.py` | Scores the model tiebreak against the signature model on each case's first dispatch |
| `train_signature_model.py` | Fits the signature model on all cases and writes `models/signature_model.yaml` |
| `setup/fetch.py` | Downloads, unzips, and renames LBNL data into `data/lbnl/` |
| `setup/make_sample.py` | Cuts the two-week sample in `data/sample/` |
| `setup/build_points_configs.py`, `setup/fpu_points_list.csv` | Wrote the blank points configs from LBNL's point descriptions (already done) |
| `ground_truth/catalog.csv` | **The labels**: every LBNL file's original and clean name, faulted unit, fault, severity, and fault-free twin. Hand-checked |
| `ground_truth/cases.yaml` | The same labels as YAML for the scorers. Generated by `apply_catalog.py`; edit the catalog instead |
| `ground_truth/build_catalog.py` | How the catalog was first built from LBNL's inventory PDFs (already run) |
| `ground_truth/find_faulted_zones.py`, `zone_findings.csv` | The evidence that LBNL's VAV faults are in zone S, not the documented W ([ADR-0001](decisions/0001-lbnl-data-over-synthetic.md)) |

## `tests/`

Run with `.venv/Scripts/python -m pytest`. No API key or download needed: model calls use fakes (`FakeLLM`, `FakeClient`).

| File | Covers |
|---|---|
| `conftest.py` | Shared fakes and fixtures: `FakeLLM`, a tiny Trend File, a minimal Site Snapshot |
| `test_tagging_graph.py` | The checkpoint pause, rejection, the one retry, end to end on a file |
| `test_siblings.py` | Finding identical units, including a box that behaves differently |
| `test_haystack.py` | Tag validation; every Reference Mapping uses legal tags |
| `test_profiles.py` | Timestamp formats and Point Profiles |
| `test_scoring.py` | Tagging scores, including rule-findable matching |
| `test_fdd.py` | Role resolution, Fault Book overrides, Candidate Windows, detection scoring |
| `test_dispatch.py` | Peer comparison, Persistence patterns, the signature model, Priority, the policy gate, operating minutes |
| `test_health.py` | Outages, frozen and constant sensors, garbage values, blank stretches |
| `test_site.py` | Work Order requests, one per unit, approve/reject, life-safety blocking |
| `test_chat.py` | The tool loop, errors returned to the model, cut-off responses, the cost cap |
| `test_credentials.py` | Finding and saving the API key without ever committing it |

## `docs/`

| | |
|---|---|
| `README.md`, `1-overview.md`, `2-architecture.md`, `3-reference.md` | This write-up |
| `glossary.md` | Project terms and the words to avoid |
| `decisions/` | Design decisions (ADR-0001 to ADR-0008) |
| `results/` | Measured results per phase |
| `history/proposal.md` | The original plan |

## `data/` and `runs/`

| | |
|---|---|
| `data/sample/` | Two weeks of LBNL data, fault-free and with a stuck damper (committed) |
| `data/lbnl/` | The full LBNL data from `benchmark/setup/fetch.py` (git-ignored) |
| `runs/` | Everything the tools write: tagging runs, Site Snapshots, benchmark outputs (git-ignored) |
