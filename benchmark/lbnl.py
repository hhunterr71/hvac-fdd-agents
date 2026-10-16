"""Shared by the benchmark scripts: the LBNL case catalog and where its files live.

The catalog (ground_truth/catalog.csv) has one row per LBNL data file: its original
name, its clean name, and its fault label. Scripts in subfolders import this with
`sys.path.insert(0, <benchmark dir>)`.
"""

import csv
from pathlib import Path

from hvac_fdd_agents import paths

CATALOG = paths.GROUND_TRUTH / "catalog.csv"
CASES = paths.GROUND_TRUTH / "cases.yaml"


def load_catalog() -> list[dict]:
    with CATALOG.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def case_file(system: str, file: str) -> Path:
    return paths.LBNL / system / file


def rename_files(systems: list[str] | None = None) -> None:
    """Rename downloaded LBNL files to their clean catalog names. Safe to re-run."""
    for row in load_catalog():
        if systems and row["system"] not in systems:
            continue
        folder = paths.LBNL / row["system"]
        old, new = folder / row["original_file"], folder / row["file"]
        if new.exists():
            continue
        if old.exists():
            old.rename(new)
        else:
            print(f"missing: {old}")
