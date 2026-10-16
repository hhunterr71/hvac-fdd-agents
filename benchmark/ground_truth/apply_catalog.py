"""Rename LBNL files to their catalog names and write cases.yaml from catalog.csv.

Usage: python benchmark/ground_truth/apply_catalog.py [SYSTEM ...]

Safe to re-run: files already renamed are left alone. cases.yaml is always
regenerated in full from catalog.csv (next to this script).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # benchmark/, for lbnl.py
from lbnl import CASES, load_catalog, rename_files  # noqa: E402


def yaml_str(value: str) -> str:
    return "null" if value == "" else '"' + value.replace('"', '\\"') + '"'


def write_cases() -> None:
    lines = [
        "# Ground truth for every LBNL case. Only the scorer may read this file.",
        "# Generated from benchmark/ground_truth/catalog.csv by apply_catalog.py; edit the catalog, not this.",
        "# unit/component/mode/severity: what failed and how. twin: fault-free file to compare against.",
        "# twin_aligned: false means fault and twin cover different dates, so only case-level scoring applies.",
        "",
        "cases:",
    ]
    for r in load_catalog():
        lines += [
            f"  - case: {r['system']}/{r['file']}",
            f"    system: {r['system']}",
            f"    fault_free: {r['fault_free'].lower()}",
        ]
        if r["fault_free"] == "False":
            lines += [
                f"    unit: {r['unit']}",
                f"    component: {r['component']}",
                f"    mode: {r['mode']}",
                f"    severity: {yaml_str(r['severity'])}",
                f"    twin: {r['system']}/{r['twin']}",
                f"    twin_aligned: {r['twin_aligned'].lower()}",
            ]
        lines.append(f"    original_file: {yaml_str(r['original_file'])}")
        lines.append(f"    source: {yaml_str(r['source'])}")
        if r["note"]:
            lines.append(f"    note: {yaml_str(r['note'])}")
    CASES.parent.mkdir(parents=True, exist_ok=True)
    CASES.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {CASES}")


if __name__ == "__main__":
    rename_files(sys.argv[1:] or None)
    write_cases()
