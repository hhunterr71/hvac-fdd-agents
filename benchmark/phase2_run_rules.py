"""Run the Fault Book on every LBNL case of a system (rules only, no model calls).

Usage: python benchmark/phase2_run_rules.py SYSTEM [SYSTEM ...] [--faultbook config/faultbook.yaml]

Uses the Reference Mapping as the approved points config, so these numbers measure the
rules, not the tagging. Writes runs/fdd/<faultbook stem>/<SYSTEM>/:
  outcomes.csv   one row per case x unit x rule: status and confirmed fault hours
  windows/       Candidate Windows per case (csv.gz)
"""

import argparse
import csv
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from lbnl import load_catalog

from hvac_fdd_agents import paths


def run_case(args: tuple[str, str, str]) -> list[dict]:
    system, file, book_path = args
    from hvac_fdd_agents.data import points_config
    from hvac_fdd_agents.rules import engine, faultbook, roles

    cfg = points_config.load(paths.points_config(system))
    book = faultbook.load(Path(book_path))
    rm = roles.load_role_map(paths.ROLES)
    windows, outcomes = engine.run(paths.LBNL / system / file, cfg, book, rm)
    out_dir = paths.RUNS / "fdd" / Path(book_path).stem / system / "windows"
    out_dir.mkdir(parents=True, exist_ok=True)
    wf, _ = engine.to_frames(windows, outcomes)
    wf.to_csv(out_dir / (file.removesuffix(".csv") + ".csv.gz"), index=False)
    return [{"case": file, "unit": o.unit, "rule": o.rule, "status": o.status,
             "fault_hours": round(o.fault_hours, 2)} for o in outcomes]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("systems", nargs="+")
    ap.add_argument("--faultbook", default=str(paths.FAULTBOOK))
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    for system in args.systems:
        cases = [r["file"] for r in load_catalog() if r["system"] == system]
        out = paths.RUNS / "fdd" / Path(args.faultbook).stem / system
        out.mkdir(parents=True, exist_ok=True)
        rows = []
        with ProcessPoolExecutor(args.workers) as pool:
            for i, result in enumerate(pool.map(run_case, [(system, c, args.faultbook) for c in cases]), 1):
                rows += result
                print(f"{system}: {i}/{len(cases)} cases", flush=True)
        with (out / "outcomes.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"wrote {out / 'outcomes.csv'}")


if __name__ == "__main__":
    main()
