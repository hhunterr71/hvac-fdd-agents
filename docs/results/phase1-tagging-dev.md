# Phase 1 results: tagging, development set

Date: 2026-10-14. Model `claude-opus-5-5`, effort `medium`. One run per cell, so run-to-run variance isn't measured yet. Scored against the Reference Mappings in `config/`. The held-out systems (SDAHU, DDAHU, FCU, BOILER, CHILLER, RTU_SIM) have not been run.

| System | Mode | Equipment | Selection | Rule-findable | Exact | Cost | Agent time |
|---|---|---|---|---|---|---|---|
| PFPU | multi (v1: one tagger per unit) | 100% | 84% | 80% | 77% | $0.42 | n/a |
| PFPU | multi (v2: prompt fixes + Sibling Units) | 100% | 94% | 91% | 64% | $0.27 | 54 s |
| PFPU | single | 100% | 94% | 92% | 77% | $0.27 | 77 s |
| SFPU | multi (v2) | 100% | 94% | 95% | 82% | $0.27 | 64 s |
| SFPU | single | 100% | 94% | 92% | 77% | $0.27 | 79 s |

## Findings

- **On 109-point systems, multi-agent and single-agent tie on accuracy and cost.** One prompt holds a 109-point system comfortably. Multi-agent is faster (parallel taggers), and it adds the Equipment Checkpoint and consistency by construction, but it doesn't add accuracy at this size. Whether accuracy splits on larger systems is an open question for the held-out set.
- **v1 → v2 was the big gain (selection 84% → 94%).** Two causes: (1) "exclude duplicates/constants" dropped points that only diverge under faults (command vs. feedback, status vs. measurement, constant setpoints); (2) four parallel taggers on four identical VAV boxes made four different keep/exclude calls. Prompt rules fixed the first. Tagging Sibling Units once fixed the second, and cut cost by a third.
- **You can't judge what to keep from healthy data alone.** Points that are redundant in fault-free operation (valve command vs. position, r = 1.00) are exactly the ones that reveal stuck-valve faults.
- **The remaining misses are honest ambiguities.** Mostly a lone damper position read as a command. Those were flagged low-confidence and routed to the Review Queue.
- **"Exact" understates quality.** Haystack queries match by subset, so extra descriptive tags (`heating`, `coil`) don't stop a rule finding a point. Rule-findable is the downstream measure: does a query for the reference tags pick this point as the unique most-specific match?
