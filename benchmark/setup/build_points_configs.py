"""Create blank points configs for every LBNL system, using the generic tool.

Usage: python benchmark/setup/build_points_configs.py

Runs `hvac-fdd-agents new-config` on each system's fault-free Trend File. PFPU and
SFPU get descriptions from fpu_points_list.csv (LBNL inventory Table 1), expanded
for the _I/_W/_S/_E zone suffixes. Existing configs are skipped.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # benchmark/, for lbnl.py
from lbnl import case_file, load_catalog  # noqa: E402

from hvac_fdd_agents import paths  # noqa: E402
from hvac_fdd_agents.data.points_config import read_points_list, write_blank  # noqa: E402

ZONES = "IWSE"

# Each system's fault-free Trend File (from the catalog); its header defines the points.
SYSTEMS = ["PFPU", "SFPU", "SDAHU", "DDAHU", "FCU", "BOILER", "CHILLER", "RTU_SIM"]


def fault_free_file(system: str) -> str:
    rows = [r for r in load_catalog() if r["system"] == system and r["fault_free"] == "True"]
    return rows[0]["file"]


def fpu_descriptions() -> dict[str, tuple[str, str]]:
    base = read_points_list(Path(__file__).with_name("fpu_points_list.csv"))
    out = dict(base)
    for point, (desc, units) in base.items():
        for z in ZONES:
            out[f"{point}_{z}"] = (f"{desc} (zone {z})", units)
    return out


def main() -> None:
    for system in SYSTEMS:
        out = paths.points_config(system)
        if out.exists():
            print(f"skip {out.name}: already exists")
            continue
        descriptions = fpu_descriptions() if system in ("PFPU", "SFPU") else None
        n = write_blank(case_file(system, fault_free_file(system)), system, out, descriptions)
        print(f"wrote {out.name} ({n} points)")


if __name__ == "__main__":
    main()
