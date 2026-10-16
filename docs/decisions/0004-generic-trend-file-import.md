# Generic Trend File import; LBNL is the benchmark, not the input format

The tool accepts any Trend File: a wide CSV with one timestamp column followed by one column per point, which is the shape most BMS trend exports already take. Nothing in the pipeline depends on LBNL file names, point names, or folder layout. LBNL-specific code (download, case labels, LBNL point descriptions) lives in `benchmark/` and exists only to build the benchmark.

This makes the project usable on someone else's building, and makes the LBNL results evidence that a general tool works rather than a demo tuned to one dataset.

## Consequences

- Timestamp format is detected, not assumed: LBNL alone uses both `MM/DD/YYYY HH:MM` and ISO `YYYY-MM-DD HH:MM:SS`.
- Units come from the points config, never from the data. LBNL mixes °F, °C, and Pa across systems.
- Long-format exports (one row per timestamp, point, and value) and one-file-per-point exports are not supported yet. They can be added as converters into the same wide shape.
