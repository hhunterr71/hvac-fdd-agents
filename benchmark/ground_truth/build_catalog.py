"""Build the LBNL case catalog: one row per data file, with clean names and labels.

Usage: python benchmark/ground_truth/build_catalog.py

Writes catalog.csv (next to this script). Already run: catalog.csv is committed and
hand-checked, so re-running is only for changing how labels are derived. Each row maps an original LBNL file to:
  file           new file name: lowercase, only [a-z0-9_.], fields split by "__"
  unit           the faulted piece of equipment (e.g. VAV-S, AHU-1, CH-1)
  component      the faulted part (e.g. reheat_valve, zone_temp_sensor)
  mode           how it failed (e.g. stuck, leaking, bias, fouling_airside)
  severity       value + unit as LBNL gives it (e.g. 20pct, neg2c, minor)
  twin           the fault-free file it is compared against
  twin_aligned   whether fault and twin share timestamps (needed for Symptom Windows)
  source         where the label comes from (LBNL inventory section, and data checks)
  note           anything a reader should know (doc errors, caveats)

Labels follow each system's LBNL inventory PDF (data/lbnl/docs/). Zone-level faults
take their unit from zone_findings.csv (find_faulted_zones.py), because the PDFs
are wrong about which zone was faulted.
"""

import csv
import re
from pathlib import Path

from hvac_fdd_agents import paths

HERE = Path(__file__).parent
DATA = paths.LBNL
OUT = HERE / "catalog.csv"
FIELDS = ["system", "original_file", "file", "fault_free", "unit", "component", "mode",
          "severity", "twin", "twin_aligned", "source", "note"]

GRADES = {"Minor": "minor", "Moderate": "moderate", "Severe": "severe"}


def num(token: str, unit: str = "") -> str:
    """'-2' + 'c' -> 'neg2c'; '+400' + 'cfm' -> 'pos400cfm'; '0.2' -> '0p2'; '20' + 'pct' -> '20pct'."""
    token = token.strip()
    sign = "neg" if token.startswith("-") else "pos" if token.startswith("+") else ""
    body = token.lstrip("+-")
    if "." not in body:
        body = str(int(body))  # drop leading zeros: 010 -> 10
    return f"{sign}{body.replace('.', 'p')}{unit}"


def signed(token: str, unit: str) -> str:
    """Bias values always carry a sign, even when LBNL omits '+'."""
    t = token.strip()
    return num(t if t[0] in "+-" else "+" + t, unit)


def load_zones() -> dict[tuple[str, str], str]:
    path = HERE / "zone_findings.csv"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return {(r["system"], r["original_file"]): r["zone"] for r in csv.DictReader(f)}


# --- Per-system rules -------------------------------------------------------------
# Each returns (unit, component, mode, severity, note) for a faulted file, or None.

def fpu(name: str, zone: str | None):
    stem = re.sub(r"^[PS]FPU_|\.csv$", "", name)
    unit = f"VAV-{zone}" if zone else None
    rules = [
        (r"ReheatCoilFouling_(Airside|Waterside)_(\w+)",
         lambda m: ("reheat_coil", f"fouling_{m[1].lower()}", GRADES[m[2]])),
        (r"ReheatVLVLeak_(\d+)%MaxFlow", lambda m: ("reheat_valve", "leaking", num(m[1], "pct"))),
        (r"ReheatVLVStuck_(\d+)%", lambda m: ("reheat_valve", "stuck", num(m[1], "pct"))),
        (r"RMTEMPUnstable", lambda m: ("zone_temp_control", "unstable", "")),
        (r"SensorBias_RMTEMP_([+-]\d+)C", lambda m: ("zone_temp_sensor", "bias", signed(m[1], "c"))),
        (r"SensorBias_VAVAirflow_([+-]\d+)CFM", lambda m: ("airflow_sensor", "bias", signed(m[1], "cfm"))),
        (r"VAVDMPRStuck_(\d+)%", lambda m: ("damper", "stuck", num(m[1], "pct"))),
        (r"VAVDMPRUnstable", lambda m: ("damper_control", "unstable", "")),
        (r"VAVFanRestrictFlow", lambda m: ("terminal_fan", "restricted_flow", "")),
    ]
    for rx, f in rules:
        m = re.fullmatch(rx, stem)
        if m:
            note = "LBNL inventory says zone W; data shows the fault in this zone."
            if "Waterside" in stem:
                unit = "VAV-S"
                note = ("No zone diverges from the twin; VAV-S assumed (every other case in this "
                        "dataset is VAV-S). Likely too faint to detect.")
            return (unit, *f(m), note)
    return None


def sdahu(name: str, zone: str | None):
    stem = name.removesuffix(".csv")
    rules = [
        (r"coi_bias_(-?\d+)_annual", "supply_air_temp_sensor", "bias", "c",
         "File is named coi_bias; inventory lists it as sa_bias (supply air temp sensor). "
         "Logged SA_TEMP is the true value, not the biased reading."),
        (r"oa_bias_(-?\d+)_annual", "oa_temp_sensor", "bias", "c",
         "Logged OA_TEMP is the true value (identical to the twin), not the biased reading."),
        (r"coi_leakage_(\d+)_annual", "cooling_valve", "leaking", "pct", ""),
        (r"coi_stuck_(\d+)_annual", "cooling_valve", "stuck", "pct", ""),
        (r"damper_stuck_(\d+)_annual(_short)?", "oa_damper", "stuck", "pct", ""),
    ]
    for rx, comp, mode, unit_tok, note in rules:
        m = re.fullmatch(rx, stem)
        if m:
            sev = signed(m[1], unit_tok) if mode == "bias" else num(m[1], unit_tok)
            if m.lastindex and m.lastindex > 1 and m[2]:
                note = "Data covers Apr 1 to Nov 1 only."
            return ("AHU-1", comp, mode, sev, note)
    return None


def ddahu(name: str, zone: str | None):
    stem = re.sub(r"^DualDuct_|_?\.csv$", "", name)
    box = f"VAV-{zone}" if zone else None
    rules = [
        (r"Fouling_(Cooling|Heating)_(Airside|Waterside)_(\w+)",
         lambda m: ("AHU-1", f"{m[1].lower()}_coil", f"fouling_{m[2].lower()}", GRADES[m[3]], "")),
        (r"SensorBias_(CSA|HSA)_([+-]\d+)C",
         lambda m: ("AHU-1", {"CSA": "cold_deck_supply_temp_sensor", "HSA": "hot_deck_supply_temp_sensor"}[m[1]],
                    "bias", signed(m[2], "c"), "")),
        (r"SensorBias_(CSP|HSP)_([+-])(\d)inwg",
         lambda m: ("AHU-1", {"CSP": "cold_deck_static_pressure_sensor",
                              "HSP": "hot_deck_static_pressure_sensor"}[m[1]],
                    "bias", signed(f"{m[2]}0.{m[3]}", "inwg"),
                    f"File name says {m[2]}{m[3]} in.wg; inventory table gives {m[2]}0.{m[3]} in.wg.")),
        (r"DMPRStuck_(Cold|Hot)_(\d+)",
         lambda m: (box, f"{'cold' if m[1] == 'Cold' else 'hot'}_deck_damper", "stuck", num(m[2], "pct"),
                    "Terminal box damper; zone from comparison with the twin.")),
        (r"DMPRStuck_OA_(\d+)",
         lambda m: ("AHU-1", "oa_damper", "stuck", num(m[1], "pct"),
                    "Inventory Table 4 lists 20%/50% for the 28/45 files; file names and Table 3 say 28%/45%."
                    if m[1] in ("28", "45") else "")),
        (r"VLVStuck_(Cooling|Heating)_(\d+)",
         lambda m: ("AHU-1", f"{m[1].lower()}_valve", "stuck", num(m[2], "pct"), "")),
        (r"(Cool|Heat)SeqUnstable",
         lambda m: ("AHU-1", f"{'cooling' if m[1] == 'Cool' else 'heating'}_control", "unstable", "",
                    "All four zones diverge equally from the twin, so this is an AHU-level fault.")),
    ]
    for rx, f in rules:
        m = re.fullmatch(rx, stem)
        if m:
            return f(m)
    return None


def fcu(name: str, zone: str | None):
    stem = re.sub(r"^FCU_|\.csv$", "", name)
    rules = [
        (r"Control_(Cooling|Heating)Reverse", lambda m: (f"{m[1].lower()}_control", "reverse_acting", "")),
        (r"Control_Unstable", lambda m: ("control", "unstable", "")),
        (r"FanOutletBlockage", lambda m: ("supply_fan", "outlet_blockage", "")),
        (r"FilterRestriction_(\d+)%", lambda m: ("filter", "restriction", num(m[1], "pct"))),
        (r"Fouling_(Cooling|Heating)_(Airside|Waterside)_(\w+)",
         lambda m: (f"{m[1].lower()}_coil", f"fouling_{m[2].lower()}", GRADES[m[3]])),
        (r"OABlockage", lambda m: ("oa_inlet", "blockage", "")),
        (r"OADMPRLeak_(\d+)", lambda m: ("oa_damper", "leaking", num(m[1], "pct"))),
        (r"OADMPRStuck_(\d+)", lambda m: ("oa_damper", "stuck", num(m[1], "pct"))),
        (r"SensorBias_RMTemp_([+-]\d+)C", lambda m: ("zone_temp_sensor", "bias", signed(m[1], "c"))),
        (r"VLVLeak_(Cooling|Heating)_(\d+)", lambda m: (f"{m[1].lower()}_valve", "leaking", num(m[2], "pct"))),
        (r"VLVStuck_(Cooling|Heating)_(\d+)", lambda m: (f"{m[1].lower()}_valve", "stuck", num(m[2], "pct"))),
    ]
    for rx, f in rules:
        m = re.fullmatch(rx, stem)
        if m:
            note = ("Inventory Table 3 labels this 'heating coil valve leaking'; file name and "
                    "Table 2 say cooling." if "VLVLeak_Cooling" in stem else "")
            return ("FCU-1", *f(m), note)
    return None


def boiler(name: str, zone: str | None):
    stem = re.sub(r"^BoilerPlant_|\.csv$", "", name)
    rules = [
        (r"boiler_bias_(-?\d+)", lambda m: ("BLR-1", "hw_leaving_temp_sensor", "bias", signed(m[1], "c"))),
        (r"hot_water_temp_bias_(-?\d+)", lambda m: ("HW-LOOP", "hw_supply_temp_sensor", "bias", signed(m[1], "c"))),
        (r"hot_water_pressure_bias_(-?\d+)", lambda m: ("HW-LOOP", "dp_sensor", "bias", signed(m[1], "pct"))),
        (r"boiler_foul_(\d+)", lambda m: ("BLR-1", "heat_exchanger", "fouling", num(m[1], "pct_ua"))),
        (r"boiler_PI", lambda m: ("HW-LOOP", "supply_temp_control", "poor_tuning", "")),
    ]
    for rx, f in rules:
        m = re.fullmatch(rx, stem)
        if m:
            note = "Pump power points are NaN in this file." if not stem.startswith(("boiler_bias", "hot_water_pressure")) else ""
            return (*f(m), note)
    return None


def chiller(name: str, zone: str | None):
    stem = re.sub(r"^ChillerPlant_|\.csv$", "", name)
    rules = [
        (r"chiller_bias_(-?\d+)", lambda m: ("CH-1", "chw_leaving_temp_sensor", "bias", signed(m[1], "c"), "")),
        (r"coolingtower_bias_(-?\d+)", lambda m: ("CT-1", "cw_leaving_temp_sensor", "bias", signed(m[1], "c"), "")),
        (r"secondary_chilled_water_pressure_bias_(-?\d+)",
         lambda m: ("CHW-LOOP", "dp_sensor", "bias", signed(m[1], "pct"), "Secondary chilled water loop.")),
        (r"bypass_leakage_(\d+)", lambda m: ("CW-LOOP", "bypass_valve", "leaking", num(m[1], "pct"),
                                             "Three-way valve on the condenser water loop.")),
        (r"bypass_stuck_(\d+)", lambda m: ("CW-LOOP", "bypass_valve", "stuck", num(m[1], "pct"),
                                           "Three-way valve on the condenser water loop.")),
        (r"coolingtower_fouling_(\d+)", lambda m: ("CT-1", "tower_heat_exchanger", "fouling", num(m[1], "pct_ua"), "")),
        (r"chiller_fouling_(\d+)", lambda m: ("CH-1", "chiller_heat_exchanger", "fouling", num(m[1], "pct_ua"),
                                              "Not in the LBNL inventory; label inferred from the file name.")),
        (r"coolingtower_PI", lambda m: ("CW-LOOP", "supply_temp_control", "poor_tuning", "", "")),
    ]
    for rx, f in rules:
        m = re.fullmatch(rx, stem)
        if m:
            return f(m)
    return None


def rtu_sim(name: str, zone: str | None):
    stem = re.sub(r"^RTU_sim_|\.csv$", "", name)
    rules = [
        (r"condfouling(\d+)", lambda m: ("condenser", "fouling", num(m[1], "pct_airflow"))),
        (r"evapfouling(\d+)", lambda m: ("evaporator", "fouling", num(m[1], "pct_airflow"))),
        (r"liquidpipe(\d+)bar", lambda m: ("liquid_line", "restriction", num(m[1], "bar"))),
        (r"suctionpipe(\d+)bar", lambda m: ("suction_line", "restriction", num(m[1], "bar"))),
        (r"overcharge(\d+)", lambda m: ("refrigerant", "overcharge", num(m[1], "pct"))),
        (r"undercharge(\d+)", lambda m: ("refrigerant", "undercharge", num(m[1], "pct"))),
    ]
    for rx, f in rules:
        m = re.fullmatch(rx, stem)
        if m:
            return ("RTU-1", *f(m), "")
    return None


def rtu_ornl(name: str, zone: str | None):
    m = re.fullmatch(r"(.+)_(Fall|Spring|Summer|Winter)_(\d{4})\.csv", name)
    stem = m[1]
    rules = [
        (r"OA_damper_stuck_(\d+)", lambda m: ("oa_damper", "stuck", num(m[1], "pct"))),
        (r"Inc_Eco_SP_(-?\d+)", lambda m: ("economizer_setpoint", "incorrect", signed(m[1], "c"))),
        (r"SA_temp_bias_(-?\d+)", lambda m: ("supply_air_temp_sensor", "bias", signed(m[1], "c"))),
    ]
    for rx, f in rules:
        mm = re.fullmatch(rx, stem)
        if mm:
            return ("RTU-1", *f(mm), "Lab test at ORNL: one day of faulted operation; twin is other days of the same season.")
    return None


def rtu_field(name: str, zone: str | None):
    if name == "Site1_Staging_Fault.csv":
        return ("RTU-1", "compressor_staging_control", "stage2_fails", "",
                "Field data, Milford CT. Twin is a later, non-overlapping period.")
    if name == "Site2_Undercharged40.csv":
        return ("RTU-1", "refrigerant", "undercharge", "40pct",
                "Field data, Colchester CT, circuit B. Twin is a later, non-overlapping period.")
    return None


# system -> (rule function, fault-free file or function(name)->twin, twin_aligned, inventory)
SYSTEMS = {
    "PFPU": (fpu, "PFPU_FaultFree.csv", True, "LBNL_FDD_Data_Sets_FPU.pdf Table 3"),
    "SFPU": (fpu, "SFPU_FaultFree.csv", True, "LBNL_FDD_Data_Sets_FPU.pdf Table 4"),
    "SDAHU": (sdahu, "AHU_annual.csv", True, "LBNL_FDD_Data_Sets_SDAHU.pdf Tables 3-4"),
    "DDAHU": (ddahu, "DualDuct_FaultFree.csv", True, "LBNL_FDD_Data_Sets_DDAHU.pdf Tables 3-4"),
    "FCU": (fcu, "FCU_FaultFree.csv", True, "LBNL_FDD_Data_Sets_FCU.pdf Tables 2-3"),
    "BOILER": (boiler, "BoilerPlant.csv", True, "LBNL_FDD_Data_Sets_Boiler_Plant.pdf Tables 3-4"),
    "CHILLER": (chiller, "ChillerPlant.csv", True, "LBNL_FDD_Data_Sets_Chiller_Plant.pdf Tables 3-4"),
    "RTU_SIM": (rtu_sim, "RTU_sim_baseline.csv", True, "LBNL_FDD_Data_Sets_RTU.pdf Tables S4-S5"),
    "RTU_ORNL": (rtu_ornl, lambda n: "ERTU_" + "_".join(n.removesuffix(".csv").split("_")[-2:]) + ".csv",
                 False, "LBNL_FDD_Data_Sets_RTU.pdf Tables E2-E3"),
    "RTU_FIELD": (rtu_field, lambda n: n.split("_")[0] + "_Unfaulted.csv", False,
                  "LBNL_FDD_Data_Sets_RTU.pdf Tables F2-F3"),
}


def is_fault_free(system: str, name: str, twin: str) -> bool:
    return name == twin or (system == "RTU_ORNL" and name.startswith("ERTU_")) or name.endswith("_Unfaulted.csv")


def new_name(system: str, fault_free: bool, component: str, mode: str, severity: str, original: str) -> str:
    parts = [system.lower()]
    if fault_free:
        parts.append("fault_free")
    else:
        parts.append(f"{component}_{mode}")
        if severity:
            parts.append(severity)
    if system == "RTU_ORNL":  # one file per season
        season, year = original.removesuffix(".csv").split("_")[-2:]
        parts.append(f"{season.lower()}_{year}")
    if system == "RTU_FIELD":
        parts.append(original.split("_")[0].lower())
    return "__".join(parts) + ".csv"


def main() -> None:
    zones = load_zones()
    # Files on disk may already carry their new names; map them back to LBNL's.
    renamed = {}
    if OUT.exists():
        with OUT.open(encoding="utf-8") as f:
            renamed = {(r["system"], r["file"]): r["original_file"] for r in csv.DictReader(f)}
    rows, problems = [], []
    for system, (rule, twin_spec, aligned, inventory) in SYSTEMS.items():
        folder = DATA / system
        originals = sorted(renamed.get((system, p.name), p.name) for p in folder.glob("*.csv"))
        for name in originals:
            twin = twin_spec(name) if callable(twin_spec) else twin_spec
            row = {"system": system, "original_file": name, "twin_aligned": aligned}
            if is_fault_free(system, name, twin):
                row.update(fault_free=True, unit="", component="", mode="", severity="",
                           twin="", source=inventory, note="")
            else:
                label = rule(name, zones.get((system, name)))
                if label is None or label[0] is None:
                    problems.append(f"{system}/{name}: no label (missing rule or zone finding)")
                    continue
                unit, component, mode, severity, note = label
                source = inventory + ("; zone from find_faulted_zones.py" if system in ("PFPU", "SFPU", "DDAHU")
                                      and (system, name) in zones else "")
                row.update(fault_free=False, unit=unit, component=component, mode=mode,
                           severity=severity, twin=twin, source=source, note=note)
            row["file"] = new_name(system, row["fault_free"], row["component"], row["mode"],
                                   row["severity"], name)
            rows.append(row)
    # Fill twin with its new file name.
    new_by_original = {(r["system"], r["original_file"]): r["file"] for r in rows}
    for r in rows:
        if r["twin"]:
            r["twin"] = new_by_original[(r["system"], r["twin"])]
    names = [(r["system"], r["file"]) for r in rows]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        problems += [f"duplicate new name: {d}" for d in sorted(dupes)]
    bad = [r["file"] for r in rows if not re.fullmatch(r"[a-z0-9_.]+", r["file"])]
    problems += [f"unsafe name: {b}" for b in bad]
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {OUT.name}: {len(rows)} files")
    for p in problems:
        print("PROBLEM:", p)


if __name__ == "__main__":
    main()
