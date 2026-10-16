# Glossary

A multi-agent facilities assistant that finds, explains, and acts on HVAC equipment faults, scored against labeled fault data with known ground truth.

## Language

### Equipment

**Unit**:
One piece of equipment (e.g. a VAV terminal) that is monitored, diagnosed, and dispatched against.
_Avoid_: device, asset, box

**Sibling Units**:
Units of the same type whose points differ only by a unit token (VAV_DMPR_S vs VAV_DMPR_E) and whose data behaves alike. They are tagged once, and the decisions are copied to the rest.
_Avoid_: duplicate units, clones

**Equipment Registry**:
The list of Units, their type, and what each one serves.
_Avoid_: asset list, inventory

**Point Role**:
The standard meaning of a measured or commanded value on a Unit, expressed as a Project Haystack tag set (e.g. `zone air temp sensor`, `damper cmd`), independent of what any BMS calls it.
_Avoid_: point name, column, custom role names

**Trend File**:
A wide CSV export from a building system: one timestamp column, then one column per point. This is the tool's only input format.
_Avoid_: data dump, export, telemetry file

**Raw Point Name**:
A point's name as it comes from the source system (e.g. `VAV3_DA_T`). It is never used by FDD Rules directly.
_Avoid_: tag, point ID

**Point Mapping**:
The reviewed assignment of a Point Role and a Unit to each Raw Point Name. A Point Mapping is either proposed (by an agent) or approved (by a person).
_Avoid_: tagging result, normalization

**Reference Mapping**:
The reviewed Haystack Point Mappings for a system, derived from the source system's own point descriptions and made before seeing any agent output. A point left out of it counts as excluded (not worth trending). It is the answer key for the tagging agent, and once approved it is the config every downstream agent runs on.
_Avoid_: ground truth tags, gold labels

**Point Profile**:
A statistical summary of a point's data (range, typical values, change rate, continuous vs. stepped, correlated points) that the tagging agent reasons over instead of raw telemetry.
_Avoid_: point stats, fingerprint

**Review Queue**:
Agent proposals (Point Mappings, Fault Book edits, Work Orders) waiting for a person to approve, edit, or reject.
_Avoid_: inbox, approval list

**Equipment Checkpoint**:
The required human approval of discovered Units, their equipment types, and feeds relationships, before any point tagging starts.
_Avoid_: equipment review, stage gate

**Site Snapshot**:
The saved result of analyzing one Trend File as of one date: Data Health, Candidate Windows, Incidents, diagnoses, and Work Orders. The chat and the dashboard read it; they don't recompute anything.
_Avoid_: report, run, state

**Data Health**:
Whether a point's data arrived and can be trusted: coverage, gaps, frozen readings, and implausible values. It is about the data, not the equipment; a fault rule judges the equipment.
_Avoid_: data quality score, sensor fault

### Detection

**FDD Rule**:
A declarative check, written in terms of Point Roles for one equipment type, that flags a Candidate Window when its condition holds for a set duration.
_Avoid_: alarm, check, detector

**Fault Book**:
The versioned set of FDD Rules per equipment type, seeded from published rules and the expert's own experience, then changed only through reviewed edits.
_Avoid_: Rule Library, rule file, ruleset

**Candidate Window**:
A span of time an FDD Rule flagged on a Unit. It may or may not be a real fault.
_Avoid_: alert, anomaly, flag

### Data and ground truth

**Fault Case**:
One LBNL time series for a single unit with one fault type at one severity, or with no fault.
_Avoid_: file, run, dataset

**Fault-free Twin**:
The no-fault Fault Case for the same unit, weather, and schedule that a faulted Fault Case is compared against.
_Avoid_: baseline, control

**Symptom Window**:
A span of time where a faulted Fault Case measurably diverges from its Fault-free Twin, meaning the fault is visible in the data. This is the unit of ground truth for detection.
_Avoid_: fault period, anomaly, event

**Remediation Rubric**:
The expert-written key that maps each fault type to its correct Work Order (trade and action). It is the only hand-written ground truth.
_Avoid_: fix mapping, answer key

### Outcomes

**Incident**:
One unit's problem over a stretch of time: all Candidate Windows on that unit that overlap or recur, merged into one item to diagnose. One open Incident per unit at a time.
_Avoid_: alarm, event, issue

**Persistence**:
How consistently an Incident's symptoms recur over a recent lookback: continuous, daily, weekly, sporadic, or stale. Only continuous, daily, or weekly Incidents are dispatched.
_Avoid_: frequency, duration

**Fault Signature**:
The set of rules firing persistently on a unit (beyond its siblings) in an Incident. Different faults leave different signatures; diagnosis starts by matching it.
_Avoid_: alarm pattern, rule set

**Watch List**:
Incidents that are real but sporadic: tracked, not dispatched, and promoted if they become persistent.
_Avoid_: backlog, low-priority tickets

**Work Order**:
A structured request sending a specific trade to a specific unit. It has one primary finding (the most likely fault, with its evidence), any other findings on that unit listed as "also observed", and a priority set by the worst finding.
_Avoid_: ticket, case, task

**Priority**:
P1–P4, computed by code from the fault's consequence (safety > equipment damage > energy > comfort), its Persistence, and its magnitude. Never set by a model.
_Avoid_: severity (that word is reserved for LBNL fault intensity)

**Dispatch**:
The decision to issue a Work Order for a unit. A Dispatch on a fault-free unit is an **Unnecessary Dispatch**.
_Avoid_: truck roll (informal only), escalation

**Work Order Outcome**:
How a dispatched Work Order closes: **confirmed** (right unit, right fault), **wrong diagnosis** (unit was faulty, but not as described), or **no fault found**. In this project it is taken from the Fault Case labels, standing in for a technician's report.
_Avoid_: resolution, ticket status

**Look-alike Faults**:
Two fault types that produce similar symptoms and must be told apart by specific evidence (e.g. airflow sensor bias vs. stuck damper).
_Avoid_: similar faults, confusable faults
