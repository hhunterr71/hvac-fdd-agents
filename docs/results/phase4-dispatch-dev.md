# Phase 4 results: Diagnose → Dispatch, development set

Date: 2026-10-14. VAV systems (PFPU and SFPU, 62 cases, 5 units each). Dispatch decisions are made at 12 month-end dates per case. Inputs are the Candidate Windows from Fault Book v1 (phase 2). The signature model is evaluated **leaving each case out**: trained on the other 61 cases, tested on the held-out one. Design choices (worst-sibling comparison, none threshold) were tuned on this development set, so these are development numbers, not held-out test numbers. Reproduce with `benchmark/phase4_dispatch.py --held-out-cases PFPU SFPU` and `benchmark/phase4_tiebreak.py`.

## Dispatch: each piece's contribution

| Variant | Faults caught (case dispatched at least once) | False dispatches, terminal boxes (per 100 box-months) | False AHU dispatches | Fault-free year |
|---|---|---|---|---|
| A. Persistence only | 100% | 100 (every box, every month) | 744 (every month) | 120 |
| B. + peer comparison (median sibling) | 100% | 90–94 | 744 | 108 |
| B. + peer comparison (worst sibling) | 97% | 55 | 744 | 70 |
| C. + signature model, none threshold 0.3 | 50% | 0 | 0 | 0 |
| **C. + signature model, none threshold 0.5 (default)** | **67%** | **15** | **0** | — |
| C. + signature model, none threshold 0.7 | 97% | 26 | 0 | — |

## Diagnosis: naming the fault

On the first dispatch per caught faulted case (40 cases):

| | Right fault named | Right trade + action |
|---|---|---|
| Signature model, first choice | 20% | 20% |
| Right fault anywhere in signature top 3 | 70% | — |
| **+ model tiebreak over the top 3, with evidence pack** | **55%** | **55%** |

Model cost: 40 calls, $0.50 (Claude Opus 5.5, medium effort).

| True fault | Cases | Signature right | Model right |
|---|---|---|---|
| Damper stuck | 10 | 1 | **10** |
| Reheat valve stuck | 7 | 3 | 6 |
| Reheat valve leaking | 3 | 0 | 2 |
| Zone temp loop unstable | 2 | 2 | 2 |
| Zone temp sensor bias | 8 | 0 | 2 |
| Airflow sensor bias | 8 | 2 | **0** |
| Restricted terminal fan | 2 | 0 | 0 |

## Findings

- **Each deterministic step removes a different kind of noise.** Persistence alone dispatches everything. Comparing a box with its *worst* sibling (not the median) halves terminal false dispatches, because real zones differ (interior vs. perimeter, solar load) and the median flagged healthy boxes. The learned "none" class removes the rest, including every AHU false dispatch: the AHU's rules fire on noise the model learned to recognize.
- **Dispatch caution is a dial, not a fixed result.** It trades caught faults against false truck rolls (50%/0 → 67%/15 → 97%/26). It's set in `config/dispatch.yaml`, so a site can choose.
- **Naming the fault is where the model earns its cost.** Rule signatures narrow it to three candidates; physical reasoning over the measurements picks the right one. Stuck dampers went from 1/10 to 10/10, because the model sees "damper flat at 0.0 while the controller calls for 532 cfm and the zone reaches 86°F". The explanations are good enough to put on the Work Order as written.
- **The model's blind spot is a look-alike: airflow sensor bias read as a stuck damper (0/8).** In these cases the logged airflow is high against setpoint and the damper sits more open than its siblings', and the model reasons "a working controller would close the damper, so it's stuck". It doesn't consider that the controller is acting on a biased reading. This is the Look-alike Faults problem the original proposal named. A Skeptic step that must argue for the closest look-alike before committing is the natural next experiment.
- **Six times the model said "none" on a truly faulty box.** As built, that would cancel a correct dispatch. The tiebreak should probably only choose *which* fault, leaving dispatch to the deterministic decision.

## Caveats

- 62 cases and one model run: no variance estimate yet.
- The tiebreak sample uses dispatches on the truly faulted box, so it measures diagnosis given the right box, not end-to-end.
- The remediation key was drafted by Claude and is not field-reviewed.
