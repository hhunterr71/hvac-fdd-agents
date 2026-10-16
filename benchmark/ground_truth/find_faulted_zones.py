"""Find which zone a fault is in by comparing each case against its fault-free twin.

Usage: python benchmark/ground_truth/find_faulted_zones.py SYSTEM [FILE_PATTERN]

For each zone suffix, scores how far that zone's own points move from the twin:
the largest per-column mean absolute difference, divided by the column's spread in
the twin. The zone with the highest score is the suggested faulted zone. Results are
appended to zone_findings.csv, which build_catalog.py reads.
Runs on the renamed files; findings are keyed by original LBNL name, which the catalog uses.
"""

import csv
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # benchmark/, for lbnl.py
from lbnl import load_catalog  # noqa: E402

from hvac_fdd_agents import paths  # noqa: E402

DATA = paths.LBNL
FINDINGS = Path(__file__).with_name("zone_findings.csv")

# System -> (fault-free file, zone suffixes)
ZONED = {
    "PFPU": ("pfpu__fault_free.csv", ["I", "W", "S", "E"]),
    "SFPU": ("sfpu__fault_free.csv", ["I", "W", "S", "E"]),
    "DDAHU": ("ddahu__fault_free.csv", ["W", "E", "SA", "SB"]),
}


def zone_scores(con, folder: Path, twin: str, case: str, zones: list[str]) -> dict[str, float]:
    cols = [r[0] for r in con.execute(
        f"select column_name from (describe select * from read_csv('{folder / twin}'))").fetchall()]
    scores = {}
    for z in zones:
        zcols = [c for c in cols if c.endswith(f"_{z}")]
        terms = [f"avg(abs(f.\"{c}\" - t.\"{c}\")) / nullif(stddev(t.\"{c}\"), 0)" for c in zcols]
        q = (f"select {', '.join(terms)} from read_csv('{folder / twin}') t "
             f"join read_csv('{folder / case}') f using (Datetime)")
        vals = [v for v in con.execute(q).fetchone() if v is not None]
        scores[z] = round(max(vals), 3) if vals else 0.0
    return scores


def main(system: str, pattern: str = "*.csv") -> None:
    twin, zones = ZONED[system]
    folder = DATA / system
    con = duckdb.connect()
    original = {r["file"]: r["original_file"] for r in load_catalog() if r["system"] == system}
    new = not FINDINGS.exists()
    with FINDINGS.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["system", "original_file", "zone", "scores"])
        for path in sorted(folder.glob(pattern)):
            if path.name == twin:
                continue
            scores = zone_scores(con, folder, twin, path.name, zones)
            zone = max(scores, key=scores.get)
            w.writerow([system, original.get(path.name, path.name), zone, " ".join(f"{k}={v}" for k, v in scores.items())])
            f.flush()
            print(system, path.name, zone, scores, flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:])
