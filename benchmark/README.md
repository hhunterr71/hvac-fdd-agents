# LBNL benchmark

Everything specific to evaluating the tool on the [LBNL FDD Data Sets](https://faultdetection.lbl.gov/data). The tool itself (`src/`) never reads anything here. Results are written up in [docs/results/](../docs/results/).

## Run it, in order

| Step | Command | Writes | Cost / time |
|---|---|---|---|
| 1. Download | `python benchmark/setup/fetch.py FPU` | `data/lbnl/PFPU`, `data/lbnl/SFPU` (~17 GB unpacked). Omit `FPU` for all systems (~53 GB) | free |
| 2. Phase 1: tagging | `hvac-fdd-agents tag data/lbnl/PFPU/pfpu__fault_free.csv --system PFPU --reference PFPU` | `runs/PFPU/multi-<time>/` | ~$0.30 per system |
| 3. Phase 2: rules | `python benchmark/phase2_run_rules.py PFPU SFPU` | `runs/fdd/faultbook/<SYSTEM>/` (Candidate Windows per case) | free; PFPU ~13 min, SFPU ~90 min |
| 4. Phase 2: score | `python benchmark/phase2_score_rules.py PFPU SFPU` | prints the numbers in `phase2-rules-baseline.md` | free, seconds |
| 5. Phase 4: dispatch | `python benchmark/phase4_dispatch.py --held-out-cases PFPU SFPU` | `runs/dispatch/held_out_cases_PFPU_SFPU/` | free, ~1 min (needs step 3) |
| 6. Phase 4: tiebreak | `python benchmark/phase4_tiebreak.py` | `runs/dispatch/tiebreak_eval/` | ~$0.50 (needs step 3) |
| 7. Retrain the model | `python benchmark/train_signature_model.py` | `models/signature_model.yaml` (used by `analyze`) | free, ~1 min (needs step 3) |

## Files

| File | What |
|---|---|
| `lbnl.py` | Shared helpers: load the case catalog, rename downloaded files |
| `phase2_run_rules.py`, `phase2_score_rules.py` | Run the Fault Book on every case; score which rules respond to which faults |
| `phase4_dispatch.py`, `phase4_tiebreak.py` | Score Incidents → diagnosis → Work Orders, and the model tiebreak |
| `train_signature_model.py` | Fit the signature model on every case and save it for `analyze` |
| `setup/fetch.py` | Download, unzip, and rename into `data/lbnl/` (step 1) |
| `setup/make_sample.py` | Cut the two-week sample in `data/sample/` |
| `setup/build_points_configs.py` | Blank points configs for every system (already done; the filled-in ones are in `config/points/`) |
| `setup/fpu_points_list.csv` | LBNL's point descriptions for the VAV systems, used by the above |
| `ground_truth/catalog.csv` | **The labels**: one row per LBNL file: original name, clean name, faulted unit, fault, severity, fault-free twin. Hand-checked |
| `ground_truth/cases.yaml` | The same labels as YAML for the scorers. Generated: edit `catalog.csv`, then run `apply_catalog.py` |
| `ground_truth/build_catalog.py` | How `catalog.csv` was first built from the LBNL inventory PDFs (already run) |
| `ground_truth/find_faulted_zones.py`, `zone_findings.csv` | Evidence that the faults are in zone S, not the W the LBNL docs say (ADR-0001) |

Only the scorers read `ground_truth/`; no agent ever sees it.
