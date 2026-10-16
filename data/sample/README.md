# Sample Trend Files

Two weeks (July 2–15 2018, 1-minute data) from the LBNL PFPU system: an AHU feeding four parallel fan-powered VAV boxes (zones I, W, S, E). Rows are copied unchanged from the full files, so these work with every command without downloading the full data set. Two weeks is one full Persistence lookback, the least that lets `analyze` decide a problem is persistent enough to dispatch.

| File | What | `analyze` result |
|---|---|---|
| `pfpu__fault_free.csv` | No fault | No Work Orders; a few units on the Watch List |
| `pfpu__damper_stuck__0pct.csv` | Same weeks; VAV-S damper stuck closed. Zone S reaches 103°F; its siblings stay near 62–73°F | Work Order for VAV-S: damper stuck (P3) |

```
.venv/Scripts/hvac-fdd-agents analyze data/sample/pfpu__damper_stuck__0pct.csv --points PFPU
.venv/Scripts/hvac-fdd-agents dashboard
.venv/Scripts/hvac-fdd-agents tag data/sample/pfpu__damper_stuck__0pct.csv --system PFPU --reference PFPU
```

Data Health flags six sensors that never change (hot-water and chilled-water entering temperatures fixed at 120°F and 45°F). That's true of the simulation: those are boundary conditions, not measurements.

Rebuild with `python benchmark/setup/make_sample.py` (needs `python benchmark/setup/fetch.py FPU` first).

Source: Lawrence Berkeley National Laboratory, LBNL FDD Data Sets, https://faultdetection.lbl.gov/data. CC BY 4.0.
