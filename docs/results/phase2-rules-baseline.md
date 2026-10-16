# Phase 2 results: rules-only baseline (Fault Book v1)

Date: 2026-10-14. Fault Book v1 = all 66 open-fdd 4.4.9 cookbook rules that apply to AHUs and VAVs, at their default thresholds. Points come from the Reference Mappings, so these numbers measure the rules, not the tagging. No model calls. 62 cases (PFPU and SFPU: 30 faulted + 1 fault-free each). Reproduce with `benchmark/phase2_run_rules.py` and `benchmark/phase2_score_rules.py`.

A rule *responds* to a fault when its confirmed fault hours on a unit exceed the same unit's hours in the fault-free twin by at least 24 h. That needs the twin, so it is an evaluation lens ("is this rule sensitive to this fault?"), not a deployable detector.

| | PFPU | SFPU |
|---|---|---|
| Background: confirmed fault hours on the **fault-free** year | 54,435 h (21 rules) | 57,125 h (19 rules) |
| Faulted cases where a rule responds on the faulted box | 77% | 80% |
| Cases where a **healthy** box also responds | 43% | 63% |
| Cases where the AHU responds | 50% | 73% |

## By fault type (share of cases detected)

| Fault | PFPU | SFPU | Rules that respond (≥50% of severities) |
|---|---|---|---|
| Damper stuck | 100% | 100% | comfort band (VAV-1), reheat-when-cool (VAV-6), others; **not** VAV-4 except at 100% open |
| Airflow sensor bias | 100% | 100% | VAV-5 (PFPU only), VAV-1, VAV-6 |
| Reheat valve stuck | 100% | 100% | SV-RATE, VAV-1, VAV-4/VAV-7 (SFPU); **not** VAV-REHEAT |
| Reheat valve leaking | 100% | 100% | SV-RATE, VAV-1 (SFPU) |
| Zone temp sensor bias | 50% | 100% | VAV-1, VAV-6, VAV-7 |
| Restricted terminal fan | 100% | 100% | SV-RATE, VAV-1, VAV-6 |
| Zone temp loop unstable | 100% | 100% | PID-HUNT-1, SV-RATE |
| Damper loop unstable | 0% | 0% | none |
| Reheat coil fouling, airside | 67% | 33% | generic rules only |
| Reheat coil fouling, waterside | 0% | 0% | none (no visible signal, as expected) |

## Findings

- **Open-source defaults on a new site are mostly noise.** The fault-free year alone produces 54–57k confirmed fault hours. The largest sources:
  - VAV-1 comfort band (70–75°F during "occupied" hours), with no occupancy point mapped, so it fires through the 55–85°F night setback;
  - VAV-7 fixed-airflow;
  - SV-FLATLINE and SV-RATE, since simulated data is smooth and flat at night;
  - FCU-MODE-CYCLE, a fan coil rule open-fdd's catalog also applies to AHUs.
- **Faults are found, but by the wrong rules, and blamed on the wrong boxes.** Most detections come from generic rules (comfort, sensor rate) rather than the fault-specific ones. VAV-REHEAT never responds to a stuck reheat valve, and VAV-4 only catches a damper stuck fully open. In 43–63% of cases a healthy box responds too, because one failing box shifts its neighbors through the shared AHU.
- **Two fault types are invisible to every rule:** waterside coil fouling (no data signature at all) and the unstable damper loop (PID-HUNT-1 sees the zone temp loop but not this one).

These are the inputs for the next two phases. The **Fault Book agent** (phase 3) tunes or disables the noisy rules and investigates why the fault-specific rules don't fire. **Diagnose → Dispatch** (phase 4) decides which box is actually broken when several respond.

Practical note: the SFPU run took about 90 minutes on 4 processes, against 13 for PFPU. Some cases are expensive for open-fdd's rules. Worth profiling before the Fault Book agent starts running repeated backtests.
