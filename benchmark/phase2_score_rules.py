"""Score rule outcomes from phase2_run_rules.py.

Usage: python benchmark/phase2_score_rules.py SYSTEM [SYSTEM ...] [--faultbook faultbook]
"""

import argparse

import pandas as pd
from lbnl import CATALOG

from hvac_fdd_agents import paths
from hvac_fdd_agents.scoring.detection import score


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("systems", nargs="+")
    ap.add_argument("--faultbook", default="faultbook")
    args = ap.parse_args()
    catalog = pd.read_csv(CATALOG, dtype=str).fillna("")
    for system in args.systems:
        base = paths.RUNS / "fdd" / args.faultbook / system
        s = score(pd.read_csv(base / "outcomes.csv"), catalog, system)
        print(f"\n=== {system} ({args.faultbook}) ===\n{s.summary()}")
        s.cases.to_csv(base / "detection_cases.csv", index=False)
        s.coverage.round(2).to_csv(base / "coverage.csv")
        s.background.round(1).to_csv(base / "background_hours.csv")
        print(f"Details: {base}")


if __name__ == "__main__":
    main()
