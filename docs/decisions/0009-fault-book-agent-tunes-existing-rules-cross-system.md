# The Fault Book agent tunes existing rules, is judged cross-system, and must beat a plain tuner

Phase 3's agent does what an FDD engineer does on a new site: it finds rules that fire on healthy equipment and tunes them, without hiding real faults. v1 is scoped so every result can be checked:

- **Edits are limited to existing rules:** disable a rule, or change its thresholds and hold time within the bounds open-fdd publishes for each parameter. Code rejects anything out of bounds. New rules (the restricted-expression format in ADR-0003) wait for a later version.
- **Feedback is Work Order Outcomes** (confirmed / wrong diagnosis / no fault found), taken from the LBNL labels, on the tuning system only.
- **The split is cross-system:** tune on PFPU and evaluate on SFPU, then the reverse. The agent never gets feedback on the system it's judged on, so it can't learn one building's quirks, and the result is what a tuned Fault Book would do on a new site.
- **It must beat a plain tuner.** A deterministic search (disable rules whose dispatches are mostly "no fault found", grid-search thresholds) gets the same backtest budget. If the agent doesn't beat it, that's reported, as in phases 1 and 4.
- **A person approves every edit.** The agent's surviving edits become a proposed Fault Book version with each edit's before/after numbers and reasoning, reviewed on a dashboard page. Nothing changes `config/faultbook.yaml` until it's approved.

## Consequences

- Backtests have to be fast. A full rules run over the VAV cases took about 100 minutes. Rules run independently, so an edit to one rule only reruns that rule, with results cached per rule and parameter set.
- Every backtest refits the signature model on the tuning system, because changing which rules fire changes the Fault Signatures.
- The headline metric is phase 4's: faults caught vs. unnecessary dispatches on the evaluation system, at the default caution setting.

## Outcome (2026-10-16)

The machinery was built and measured with the plain tuner ([results](../results/phase3-tuning-dev.md)). Tuned on SFPU, one edit (`VAV-1` `zone_lo` 70 → 62.5 °F) took PFPU from 57% to 97% of faults caught and cut unnecessary dispatches from 27.5 to 17.6 per 100 box-months. Tuned on PFPU, nothing helped: the signature model fit there already suppresses the noise.

So threshold tuning is a search problem, and a plain search handles it. The agent is built and tested but parked, without being evaluated: spending API calls to repeat a search wasn't worth it. The plain tuner is phase 3's tuning engine for now. When an agent returns here, its job should be one search can't do: set up a new building's Fault Book from its points and data before any outcomes exist. Diagnosis, the actual weak spot, is better attacked with physics features in the signature model.
