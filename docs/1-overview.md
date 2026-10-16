# 1. Overview

HVAC FDD Agents is a facilities assistant for building HVAC data. You give it a trend export from a building management system (BMS). It works out what every point is, checks whether the data can be trusted, finds equipment faults, decides which ones are worth sending a technician for, and answers questions about all of it in plain language. Where a decision matters, a person signs off.

It's an experimental prototype, evaluated on public labeled data. It never writes to a real building system.

## The problem

Buildings produce far more data than anyone has time to review. Turning that data into fixed equipment takes three skilled, slow steps:

1. **Making sense of the points.** Every BMS names points differently (`VAV3_DA_T`, `RM_TEMP_S`, ...). Before any analysis, someone has to map each one to what it measures.
2. **Running fault rules without drowning in alarms.** Published rules (ASHRAE Guideline 36) catch real faults, but at default thresholds they also fire constantly on healthy equipment.
3. **Deciding what deserves a truck roll.** One failing box makes its neighbors look odd too, and some faults look alike. Someone has to pick the box that's really broken, name the likely problem, and send the right trade.

Each step is a place an agent can help, with a person checking the work.

## What it does

| | What happens | Who decides |
|---|---|---|
| **Tag** | Agents read a statistical profile of every point and propose the equipment (an air handler, its VAV boxes) and Project Haystack tags for each point | Agents propose; a person approves the equipment first, and unsure tags go to a review queue |
| **Check the data** | Code checks for missing rows, gaps, frozen sensors, and impossible values | Code |
| **Detect** | Fault rules from open-fdd (which implements Guideline 36) flag suspect windows of time on each unit | Code |
| **Diagnose and dispatch** | Code merges the flags per unit, compares each box with its identical siblings, keeps only problems that keep recurring, ranks the likely fault, and writes a Work Order with priority, trade, and action | Code; a model breaks close calls between look-alike faults |
| **Tune the rules** | A tuner finds the rules that sent technicians to healthy equipment and backtests changes (a threshold, a hold time, turning a rule off), judged on a building it never saw. A plain search does this today; an agent version is built but parked | The tuner proposes; a person approves each new Fault Book version |
| **Answer questions** | A chat assistant (terminal or dashboard) answers "How is my system performing?", "How is connectivity behaving?", "Why is VAV-S flagged?", citing the numbers. It can request a Work Order | The assistant requests; a person approves |

Rule tuning (phase 3): tuned on one building, one rule change took the other building from 57% to 97% of faults caught, with a third fewer wasted dispatches. A plain search found it, so the agent version is parked until there's a job only an agent can do ([results](results/phase3-tuning-dev.md)).

## How you use it

```
hvac-fdd-agents tag trend.csv --system MYBLDG            # agents propose the points config; you approve it
hvac-fdd-agents analyze trend.csv --points MYBLDG        # data health, faults, Work Orders -> a Site Snapshot
hvac-fdd-agents dashboard                                 # browse it and ask questions (or: hvac-fdd-agents chat)
```

The only input is a **Trend File**: a CSV with a timestamp column and then one column per point, which is the shape most BMS exports already have. Nothing depends on a particular vendor or naming scheme.

## How well it works

All of this is measured on the [LBNL FDD Data Sets](https://faultdetection.lbl.gov/data): simulated buildings where every fault is labeled, so every result can be checked. So far it's measured on the development set (two VAV systems, 62 cases). See [results/](results/) for the details and caveats.

| Step | Result |
|---|---|
| Tagging | 100% of equipment found; 94% of points correctly kept or excluded; ~91–95% tagged so a fault rule finds them. About $0.27 per 109-point system |
| Fault rules alone | Respond to 77–80% of faulted cases, but also log 54–57k fault hours on a year with no faults at all |
| Dispatch (default setting) | 67% of faults get a Work Order, with ~15 unnecessary dispatches per 100 box-months and none on the air handler. This is a dial: 50%/0 at the cautious end, 97%/26 at the eager end |
| Naming the fault | 20% right from rule patterns alone; 55% when a model reads the evidence and breaks the tie |
| Chat | A typical question costs a few cents (about $0.07 for "How is my system performing?") |

## What it isn't

- **Not production software.** It's a prototype, evaluated on simulated data. Real sites have messier data than LBNL's.
- **Not a controller.** Agents can't change a setpoint or touch life-safety equipment. A policy gate written in code blocks that, whatever a model says.
- **Not multi-user.** The dashboard has no login, so by default only your own computer can open it.

## Built with

Python, the Anthropic API (Claude), LangGraph for the tagging workflow, DuckDB for the data, open-fdd for fault rules, Streamlit for the dashboard.
