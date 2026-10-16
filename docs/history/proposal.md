# HVAC FDD Agents: Project Proposal

> **History.** This is the original plan (October 2026), kept as written apart from status updates. For how the tool works now, see [1-overview.md](../1-overview.md).

A multi-agent facilities assistant that takes a building's trend data, tags it with Project Haystack, runs fault rules, helps manage those rules, and turns real faults into Work Orders. It is evaluated on public, labeled fault data, so every result can be checked.

Status: design settled (October 2026), phases 1, 2 and 4 built and measured; phase 3 next. Decisions are recorded in [docs/decisions/](../decisions/) and terms in [the glossary](../glossary.md).

---

## 1. Why this project

**Goal:** a portfolio project that shows multi-agent orchestration, human-in-the-loop governance, and honest evaluation, in a domain I know well (building controls and fault detection).

**The customer problem:** building operators have far more data than people to review it. Getting value from it takes three skilled, slow steps: normalizing point names, keeping fault rules tuned to the site, and deciding which alarms deserve a truck roll. Each step is a place an agent can help, with a person signing off.

**Target audiences:**
- Salesforce Forward Deployed Engineer: onboarding a messy customer system onto a standard model, then conversational agents that take governed actions (Work Orders).
- Onebrief Outcome Engineer: multi-agent orchestration, policy-gated actions, deterministic evals, and a detect → dispatch → feedback → improve loop.
- General AI-engineering roles: a shipped, tested, documented agentic system.

**Honest-framing rules:**
- Only public data. No client data from DB Engineering is used anywhere.
- No synthetic or rearranged data. When the data can't support a feature, the feature is cut ([ADR-0001](../decisions/0001-lbnl-data-over-synthetic.md)).
- Hand-made pieces are disclosed: the Reference Mappings are derived from LBNL's point descriptions (drafted with Claude, spot-checked by me), and the remediation key is expert-written.
- Nothing writes to a real building system.

---

## 2. What it does

```
Trend File (CSV) ──▶ 1. Tagging agents ──▶ points config (Haystack) ──▶ 2. Fault Book + rule engine
                     Equipment Agent                ▲                          │
                     ── Equipment Checkpoint ──     │ human approves           ▼
                     Point Taggers (parallel)       │                   Candidate Windows
                                                                               │
              3. Fault Book agent ◀── Work Order Outcomes ◀── 4. Diagnose ──▶ Dispatch ──▶ Policy Gate ──▶ Work Order
                 (noisy rules, gaps,      (technician feedback)
                  system variants)
```

| Phase | Agent(s) | Input | Output | Human checkpoint |
|---|---|---|---|---|
| 1 | **Equipment Agent → Point Taggers** (LangGraph fan-out, one tagger per unit) | Raw point names + Point Profiles (range, change rate, correlations) | Points config: units, equipment types, feeds, Haystack tags, excluded points with reasons | Equipment Checkpoint before tagging; Review Queue for low-confidence tags |
| 2 | None (deterministic rule engine) | Approved points config + Fault Book | Candidate Windows per unit | — |
| 3 | **Fault Book agent** | Rule firing stats, coverage, Work Order Outcomes | Proposed Fault Book edits with backtest evidence | Every edit is approved in the Review Queue |
| 4 | **Diagnose → Dispatch** | Candidate Windows + surrounding data | Diagnosis with cited evidence, structured Work Order | Policy gate (approve / needs human / block) |
| — | **Chat front end** (router) | "How are the VAVs doing?", "Investigate VAV-S", "Open work orders" | Routes to the agents above | — |

**Why multi-agent:** separation of information and permissions, not role-play. Tagging agents see only point names and stats. Detection and diagnosis agents never see fault labels (they live in a separate file only the scorer reads). Only the Dispatch step can create Work Orders, and only through the gate.

---

## 3. Data

**Source:** [LBNL FDD Data Sets](https://faultdetection.lbl.gov/data) (CC BY 4.0), billed as the largest public labeled HVAC fault dataset. All seven systems are downloaded: fan-powered VAV terminals (parallel and series), single- and dual-duct AHUs, fan coil units, chiller plant, boiler plant, and RTUs. That's 310 files (296 faulted cases, 14 fault-free), ~53 GB, 1-minute data, mostly a full year each. Every file is renamed to a clean, consistent name and labeled in one catalog (unit, component, failure mode, severity, fault-free twin), with the source of each label.

**Shape per system:** one fault-free twin CSV, one CSV per fault and severity, one hand-tagged points config, and ground-truth labels kept separately ([ADR-0001](../decisions/0001-lbnl-data-over-synthetic.md)).

**The tool is not LBNL-specific.** It takes any Trend File (timestamp column, then one column per point). LBNL is the benchmark that proves it works ([ADR-0004](../decisions/0004-generic-trend-file-import.md)).

**What the data showed so far:**
- Faulted and fault-free files share identical timestamps and weather, so each fault's **Symptom Windows** (when it is actually visible) can be found by comparing against the twin. That fixes LBNL's labels, which call a fault present all year even when it can't be seen.
- Each VAV case is a connected system: one AHU plus four boxes. A fault in one box shows up at the AHU and faintly in its neighbors.
- **The LBNL documentation says every VAV fault is in zone W; the data puts all of them in zone S.** Ground truth follows the data. The inventories had several more errors (wrong labels, missing files, inconsistent units), each corrected and noted in the catalog.
- The ORNL lab and field RTU cases have no time-aligned twin, so they get case-level scoring only.
- The 6 waterside reheat coil fouling cases show no visible difference from fault-free. They are probably undetectable, and will be reported that way.

---

## 4. Phase 1: Haystack tagging

Two stages in LangGraph:
1. **Equipment Agent** groups raw points into units, assigns equipment types, and finds feeds relationships (`ahuRef`). A person approves this at the **Equipment Checkpoint** before anything else runs.
2. **Point Taggers** run in parallel, one per unit, each limited to the Haystack point types valid for its equipment type. They tag each point or exclude it with a reason (e.g. simulation internal, duplicate).

A deterministic validator rejects tag sets that aren't valid Haystack. Low-confidence tags go to the Review Queue.

**Identical equipment is tagged once:** units that differ only by a unit token in their point names (and whose data behaves alike) are tagged by one agent, and the decisions are copied, which keeps them consistent and cheaper.

**Dev/test split:** PFPU and SFPU are the development set (prompts are tuned on them); the other six systems are held out for final scores only.

**Scoring:** against the Reference Mapping: a points config derived from LBNL's official point descriptions (drafted with Claude, spot-checked by me). The tagging agent never sees those descriptions, only raw point names and data, so the test is whether it can recover what the documentation says. Scored separately on equipment grouping, keep/exclude selection, and tags. Also compared against a single agent that sees everything at once.

---

## 5. Phases 2–3: Fault Book and the Fault Book agent

**Don't reinvent the wheel.** The Fault Book is seeded from [open-fdd](https://github.com/bbartling/open-fdd) (MIT), which implements ASHRAE Guideline 36 AHU fault conditions plus VAV, fan coil, RTU, and plant rules, already using Haystack-style point roles. open-fdd is a pinned library; the Fault Book is YAML that turns its rules on or off, overrides thresholds and hold times, and adds custom rules in a restricted expression format where open-fdd has gaps ([ADR-0003](../decisions/0003-fault-book-from-g36-with-simulated-tech-feedback.md)).

**The Fault Book agent** does what an FDD engineer does on a real site:
- finds rules that are too noisy, and proposes a tighter threshold, longer hold, or applies-when condition;
- finds coverage gaps (equipment or fault types no rule would catch);
- proposes variants for unusual systems.

It learns from **Work Order Outcomes** (confirmed / wrong diagnosis / no fault found), which stand in for a technician's close-out report and are taken from the LBNL labels. It gets outcomes on a tuning subset of cases and is judged on held-out cases, so "the agent improved the Fault Book" is a measured result.

---

## 6. Phase 4: Diagnose and Dispatch (to be designed)

Takes Candidate Windows, uses tools to pull surrounding data, diagnoses against the fault taxonomy with cited evidence, and drafts a Work Order (trade, action, unit). A deterministic **policy gate** classifies each action: reporting-only is approved, parameter changes need a human, and anything touching safety equipment is blocked, always. Gate rules live in config and are tested with an adversarial suite.

Design details (Look-alike Faults such as airflow sensor bias vs. stuck damper, whether a Skeptic agent earns its place, the remediation key) will be settled once phases 1–2 produce real Candidate Windows.

---

## 7. Evaluation

Per Fault Case, scored as a chain: **Detect → Diagnose → Dispatch**.

| Metric | Definition |
|---|---|
| Detection | Symptom Windows overlapped by a flag; time to detect within the window |
| False flags | Flags outside any Symptom Window, including on the healthy boxes in every case |
| Diagnosis accuracy | Top-1 / top-3 fault type vs. label |
| Dispatch accuracy | Work Order matches the expert remediation key |
| Unnecessary Dispatches | Work Orders on fault-free units (wasted truck rolls) |
| Policy compliance | Blocked actions executed (must be 0) |
| Tagging accuracy | Equipment grouping, keep/exclude selection, and rule-findable tags (would a fault rule querying the reference tags find exactly this point?) vs. the Reference Mapping |
| Cost, latency, variance | Tokens, wall-clock, spread over 5 repeats |

**Comparisons:** rules only (open-fdd defaults) → rules + agents → after Fault Book agent tuning; and single agent vs. multi-agent for tagging and diagnosis. Results are reported honestly, including where multi-agent doesn't win.

**Open guard:** every VAV fault is in the same box, so anything that sees several cases could learn "VAV-S is always broken." Shuffling perimeter zone labels per case (names only, no values) is proposed and will be decided before the first evaluation run.

---

## 8. Stack

Python, LangGraph (orchestration, human-approval interrupts), DuckDB over local files (Databricks-compatible layout, deferred; [ADR-0002](../decisions/0002-langgraph-and-local-duckdb.md)), Project Haystack, open-fdd, Anthropic API.

---

## 9. Build order

1. ✅ Data downloaded, renamed, cataloged; ground truth for all systems; points config templates.
2. ✅ Reference Mappings for all 8 simulated systems (514 points: 495 tagged, 19 excluded). Spot-check pending.
3. ✅ Phase 1: tagging agents built and measured on the VAV development set (docs/results/phase1-tagging-dev.md).
4. ✅ Phase 2: open-fdd + Fault Book v1 + rule engine; rules-only baseline on the VAV systems (docs/results/phase2-rules-baseline.md).
5. ✅ Phase 4: Diagnose → Dispatch with the policy gate; measured on the VAV development set (docs/results/phase4-dispatch-dev.md).
6. Phase 3: Fault Book agent with technician feedback.
7. 🔨 Chat front end and dashboard built: `analyze` -> Site Snapshot, chat with read-only tools, Streamlit app, Work Orders approved by a person ([ADR-0008](../decisions/0008-site-snapshot-chat-and-dashboard.md)). Full evaluation, write-up, demo pending.
8. Extend from VAV terminals to single-duct AHU, then fan coils and plants.

Phase 4 comes before phase 3 because it is the headline result and builds the scorer the Fault Book agent needs. If time runs short, the Fault Book agent is the stretch goal.

---

## 10. Risks and open questions

- **Scope.** Cut order if needed: chat front end, then Fault Book agent, then extra equipment. Keep tagging, rules baseline, Diagnose → Dispatch, and the gate.
- **Simulated data is clean.** LBNL is simulation; real sites have dropouts and stale values. Mitigated in the write-up, not with made-up noise.
- **open-fdd is alpha.** Pin the version; wrap it so a change only touches one module.
- **Single-zone labeling shortcut** (see section 7).
- **Open:** model choice per agent; the remediation key for Dispatch scoring; whether to add a Salesforce Field Service integration for Work Orders.

---

## 11. Resume line (add only once built and working)

> **HVAC FDD Agents** (Python, LangGraph, Anthropic API, Project Haystack): Multi-agent facilities assistant that tags BMS points with Haystack, runs ASHRAE Guideline 36–based fault rules, tunes them from technician feedback, and dispatches policy-gated work orders; evaluated on LBNL's public labeled fault datasets.
