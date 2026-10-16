# One Work Order per unit, and only persistent problems are dispatched

Every unit has at most one open Incident and one Work Order. All Candidate Windows on that unit merge into it. The Work Order leads with the most likely fault and its evidence, lists other findings on the unit as "also observed", and takes its Priority from the worst finding. Three tickets on one box means three truck rolls for what is usually one visit.

A finding is dispatched only if it is persistent over a recent lookback: continuous, daily, or weekly. Sporadic findings go to the Watch List; findings that have stopped are closed. A fault that showed once a month ago doesn't justify sending someone.

Priority and Persistence are computed by code, not by a model, so dispatch decisions are consistent and auditable. The model's role in phase 4 is choosing between look-alike faults when the evidence is close, and writing the explanation.

## Consequences

- Scoring dispatches needs a point in time: "would we dispatch as of this date, given the last N days?". LBNL faults are present all year, but their symptoms are seasonal, so the same case can correctly be dispatched in January and not in July.
- Secondary findings on a Work Order are judged separately from the primary diagnosis. A correct primary with wrong "also observed" notes is still a correct dispatch.
