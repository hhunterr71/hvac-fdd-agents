# Diagnosis is deterministic first; the model only decides close calls

Phase 4 runs as a pipeline of code steps with one optional model step:

1. Candidate Windows become daily fault minutes per unit and rule.
2. Peer comparison: a box with identical siblings only counts minutes above its siblings' median, so building-wide noise cancels and the box that is worse than its peers stands out.
3. Persistence (config/dispatch.yaml) decides whether an Incident is dispatchable.
4. A learned Fault Signature model (naive Bayes over which rules persist) ranks fault types. It is trained on one system and tested on another, and includes a "none" class learned from healthy boxes, so it can suppress a dispatch.
5. Only when the top two faults are within 20 points does a model read an evidence pack (the unit's measurements vs. its siblings') and choose.
6. Priority, the remediation lookup, and the policy gate are code.

We chose this because most diagnosis is counting and comparing, which code does reproducibly and for free. The model's judgment is spent where rule signatures can't separate look-alike faults. Each step is scored as a separate variant (persistence only, + peer comparison, + signature model, + model tiebreak), so the value of the model is measured, not assumed.

## Consequences

- The signature model only knows fault types present in its training system. A fault type it has never seen will be misnamed; the evidence pack and tiebreak are the safety net.
- Peer comparison needs identical siblings. Units without them (an AHU, a single RTU) fall back to raw rule minutes and stay noisy until the Fault Book agent (phase 3) tunes their rules.
