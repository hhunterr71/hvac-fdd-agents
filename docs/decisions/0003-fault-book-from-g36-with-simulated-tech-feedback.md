# Seed the Fault Book from ASHRAE Guideline 36; improve it from simulated technician feedback

We don't write fault rules from scratch. The Fault Book is seeded from open-fdd (MIT-licensed; its rule catalog implements ASHRAE Guideline 36 AHU fault conditions plus VAV, FCU, RTU, and plant rules, using Haystack point semantics). We use open-fdd rather than Guideline 36 directly because the guideline text is paywalled, and thresholds recalled from memory can't be cited honestly. The project's value is the Fault Book agent, which finds noisy rules, coverage gaps, and system-specific variants, and proposes changes for human review.

The agent learns whether a rule is wrong, rather than just loud, from Work Order Outcomes (confirmed / wrong diagnosis / no fault found). These are taken from the LBNL Fault Case labels, standing in for a technician's close-out report. The agent receives outcomes for a tuning subset of cases only. Its effect is measured on held-out cases it never received feedback for, so "the agent improved the Fault Book" is a measured result, not a claim.

open-fdd is used as a pinned Python library, not translated and not deployed as its full platform. The Fault Book YAML configures open-fdd's rule functions: which are enabled, threshold overrides, confirmation delays, and per-system variants. Rules for gaps open-fdd doesn't cover use our own restricted-expression format. A small mapping translates our Haystack tag sets into open-fdd's role names.

## Consequences

- Thresholds tuned against this data are acceptable; the held-out split is what keeps the reported numbers honest.
- Where Guideline 36 doesn't cover an equipment type, the gap is filled by agent-proposed, human-approved rules, and those rules are labeled as such in their `source` field.
