# Phase 3 results: tuning the Fault Book, development set

Date: 2026-10-16. Status: **the tuning machinery is built and measured with the plain tuner. The Fault Book agent is built and tested but hasn't been evaluated** (see "Where this leaves phase 3"). VAV systems only (PFPU and SFPU, 31 cases each). Design in [ADR-0009](../decisions/0009-fault-book-agent-tunes-existing-rules-cross-system.md). Reproduce with `benchmark/phase3_tune.py` (steps in [benchmark/README.md](../../benchmark/README.md)).

Each tuner tunes on one system, using Work Order Outcomes from its labels, and is judged on the other system, which it never got feedback from. The score it tunes against is faults caught (%) minus unnecessary terminal-unit dispatches per 100 box-months. The signature model is fit on the tuning system, so the default results here differ from phase 4's, which left one case out of both systems at a time.

## The machinery works

- **The backtester reproduces phase 4 exactly.** Leaving one case out over both systems at the defaults, it gives 66.7% caught, 15.5 unnecessary per 100 box-months, 0 on the AHU, and 320 faulty-unit dispatches, the same as [phase4-dispatch-dev.md](phase4-dispatch-dev.md). Checked with `phase3_tune.py --check`.
- **Backtests are fast.** Every rule setting is cached per case, so a full backtest of a 31-case system takes seconds once its rules are cached, and an edit only reruns the rule it changes. The first full cache build took about 13 minutes per system. No case is unusually slow; the slowest rule is SV-RATE, at about 30% of the time.

## Plain tuner results (25 backtests each way)

**Tune on SFPU, judge on PFPU:**

| | Faults caught | Unnecessary per 100 | Right fix | Confirmed / wrong diagnosis / no fault found |
|---|---|---|---|---|
| Defaults, PFPU (held-out) | 57% | 27.5 | 19% | 24 / 104 / 310 |
| **Plain tuner, PFPU (held-out)** | **97%** | **17.6** | 25% | 41 / 126 / 199 |
| Defaults, SFPU (tuning) | 77% | 12.8 | 47% | 93 / 105 / 144 |
| Plain tuner, SFPU (tuning) | 97% | 10.7 | 36% | 79 / 140 / 121 |

Edits kept: **`VAV-1` (comfort band) `zone_lo` 70 → 62.5 °F**, plus disabling `VAV-7` (fixed airflow) and `VAV-AHU-LEAVE`. The first does nearly all the work, and for a physical reason: with no occupancy point mapped, the rule judges zones against 70–75 °F around the clock, so normal night setback reads as a fault.

**Tune on PFPU, judge on SFPU:** the plain tuner found nothing that helped. At the defaults this direction already has almost no unnecessary dispatches (3 in total on each system): the signature model fit on PFPU is cautious and suppresses the noise itself. Faults caught: 43% (PFPU), 57% (SFPU).

**The SFPU-tuned edits don't hurt the other direction.** Applied there, faults caught and unnecessary dispatches are unchanged. Right fix moves 58% → 50% on PFPU and 22% → 26% on SFPU.

## Findings

- **Whether rule noise causes wasted dispatches depends on the diagnosis model.** Peer comparison and the signature model's "none" class remove much of the 54–57k hours of default-rule noise. How much is left depends on which system the model learned from. The premise of ADR-0009, that noisy rules send technicians to healthy equipment, holds in one direction and not the other.
- **A plain search is enough for thresholds.** The biggest win is one parameter on one rule, found in a handful of cheap backtests. An agent doing the same search would cost API calls to find the same thing.
- **The real weakness is naming the fault.** In every run, most dispatches to a faulty unit name the wrong fault: the right trade and action go out 19–58% of the time. Tuning thresholds barely moves that, and it can lower it.

## Where this leaves phase 3

The agent ([tuning/agent.py](../../src/hvac_fdd_agents/tuning/agent.py)) is built and unit-tested: its bounds, budget, and keep-only-what-was-backtested rule are all checked by code. It wasn't run, because the plain tuner's results show threshold search isn't where a model adds value. Ideas for when this is picked up again, best first:

1. **Set up a new building (an agent with no labels).** Before any Work Orders exist, read the points config and data and configure the Fault Book for the site: notice there's no occupancy point, read the real setpoints, switch off rules whose points are missing. Judge it against the defaults and the label-tuned plain tuner. Search can't do this, because it needs labels.
2. **Physics features for diagnosis (no model calls).** Add the evidence table's measurements (damper vs. siblings, airflow vs. setpoint) to the signature model's inputs. This targets the actual weakness, including look-alikes like airflow sensor bias vs. stuck damper.
3. **The evidence tiebreak on every dispatch.** It already raised correct fault names from 20% to 55% on close calls in phase 4. Worth re-measuring after idea 2.

The plain tuner's SFPU-tuned proposal is waiting on the dashboard's Fault Book page for review.
