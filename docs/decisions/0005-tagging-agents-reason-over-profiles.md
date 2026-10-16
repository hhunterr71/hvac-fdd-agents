# Tagging agents reason over precomputed Point Profiles, not tool calls

The tagging agents don't explore the data with tools. Deterministic code profiles every point first (range, typical values, change rate, discrete values, correlated points; about 10 s for a year of 1-minute data in DuckDB). Each agent then makes one structured-output call over those profiles. LangGraph orchestrates the stages: the Equipment Agent, the Equipment Checkpoint (a human-approval interrupt), and one Point Tagger per group of Sibling Units, in parallel.

We chose this over tool-using agents because the evidence an engineer needs to tag a point is the same for every point. Computing it once is cheaper, faster, reproducible, and testable, and a tool loop would spend tokens re-deriving it. The multi-agent structure is justified by context isolation: each tagger sees only its unit's points and never the point descriptions or the answer key. Every output is checked by a deterministic validator, with one retry that lists the problems.

Sibling Units are tagged once. Units with the same type, the same generic point names (unit token stripped), and the same profile shape per point (constant, discrete, or continuous) form a group. One tagger tags the representative, and code copies its decisions to the others. The first PFPU run showed why: four independent taggers on four identical VAV boxes made four different keep/exclude calls. Templating makes them consistent by construction and cuts PFPU from 5 tagger calls to 2. A box whose data behaves differently, such as a stuck damper reading constant, fails the profile check and is tagged on its own.

Tagging is evaluated with a development/test split. PFPU and SFPU (the VAV systems) are the development set: prompts are tuned against their Reference Mappings. SDAHU, DDAHU, FCU, BOILER, CHILLER, and RTU_SIM are held out and run only for final reporting, so reported tagging scores aren't the product of tuning on the answer key.

## Consequences

- Agents can't ask follow-up questions of the data (e.g. "show me VAV_DMPR_S during occupied hours"). If scores show that limits tagging, a tool for targeted queries is the next step, measured against this baseline.
- The single-agent baseline gets identical inputs and the same validator, so single-agent vs multi-agent comparisons isolate the orchestration.
