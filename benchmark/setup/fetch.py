"""Download the LBNL FDD Data Sets and unpack them into data/lbnl/.

Usage: python benchmark/setup/fetch.py [SYSTEM ...]   (FPU = the VAV development set)

Each zip is downloaded to data/raw/zips/, extracted into data/lbnl/<SYSTEM>/, then deleted.
Files are then renamed to their clean catalog names (benchmark/ground_truth/catalog.csv).
Inventory PDFs go to data/lbnl/docs/. Already-extracted systems are skipped.
Source: https://faultdetection.lbl.gov/data (CC-BY 4.0).
"""

import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # benchmark/, for lbnl.py
from lbnl import rename_files  # noqa: E402

from hvac_fdd_agents import paths  # noqa: E402

BASE = "https://fdddata.lbl.gov/data"
DATASETS = {
    "FPU": "Simulated_LBNL_FDD_Data_Sets_FPU/LBNL_FDD_Data_Sets_FPU",
    "SDAHU": "Simulated_LBNL_FDD_Data_Sets_SDAHU/LBNL_FDD_Data_Sets_SDAHU",
    "DDAHU": "Simulated_LBNL_FDD_Data_Sets_DDAHU/LBNL_FDD_Data_Sets_DDAHU",
    "FCU": "Simulated_LBNL_FDD_Data_Sets_FCU/LBNL_FDD_Data_Sets_FCU",
    "CHILLER": "Simulated_LBNL_FDD_Data_Sets_Chiller_Plant/LBNL_FDD_Data_Sets_Chiller_Plant",
    "BOILER": "Simulated_LBNL_FDD_Data_Sets_Boiler_Plant/LBNL_FDD_Data_Sets_Boiler_Plant",
    "RTU": "Simulated_LBNL_FDD_Data_Sets_RTU/LBNL_FDD_Data_Sets_RTU",
}

# Download name -> the system folders it unpacks into.
UNPACKS = {"FPU": ["PFPU", "SFPU"], "RTU": ["RTU_SIM", "RTU_ORNL", "RTU_FIELD"]}

# Inventory PDFs whose server filename doesn't follow the zip's name.
PDF_NAMES = {"DDAHU": "LBNL%20FDD%20Data%20Sets_DDAHU.pdf"}

# Folder names inside the zips -> our system folder names.
FOLDERS = {
    "LBNL_FDD_Data_Sets_PFPU": "PFPU",
    "LBNL_FDD_Data_Sets_SFPU": "SFPU",
    "LBNL_FDD_Dataset_SDAHU": "SDAHU",
    "LBNL_FDD_Dataset_DDAHU": "DDAHU",
    "LBNL_FDD_Dataset_FCU": "FCU",
    "LBNL_FDD_Dataset_Boiler_Plant": "BOILER",
    "Simulated RTU": "RTU_SIM",
    "ORNL_RTU": "RTU_ORNL",
    "Field RTU": "RTU_FIELD",
}

ZIPS = paths.DATA / "raw" / "zips"
OUT = paths.LBNL
DOCS = OUT / "docs"


def download(url: str, dest: Path) -> None:
    if dest.exists():
        return
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url) as resp, open(tmp, "wb") as f:
        ctype = resp.headers.get("Content-Type", "")
        if "html" in ctype:
            raise RuntimeError(f"got HTML instead of a file (login required?): {url}")
        shutil.copyfileobj(resp, f, length=1 << 20)
    tmp.rename(dest)


def fetch(system: str) -> None:
    stem = DATASETS[system]
    marker = OUT / f".{system}.done"
    if marker.exists():
        print(f"{system}: already done")
        return
    folder, name = stem.rsplit("/", 1)
    pdf = PDF_NAMES.get(system, f"{name}.pdf")
    download(f"{BASE}/{folder}/{pdf}", DOCS / f"{name}.pdf")
    zpath = ZIPS / f"{name}.zip"
    print(f"{system}: downloading zip ...", flush=True)
    download(f"{BASE}/{stem}.zip", zpath)
    print(f"{system}: extracting ...", flush=True)
    tmp = OUT / f"_tmp_{system}"
    with zipfile.ZipFile(zpath) as z:
        z.extractall(tmp)
    subdirs = [p for p in tmp.iterdir() if p.is_dir()]
    if subdirs:
        for d in subdirs:
            d.rename(OUT / FOLDERS[d.name])
        shutil.rmtree(tmp)
    else:  # the chiller zip has no top-level folder
        tmp.rename(OUT / system)
    zpath.unlink()
    rename_files(UNPACKS.get(system, [system]))
    marker.touch()
    print(f"{system}: done", flush=True)


def main(systems: list[str]) -> None:
    ZIPS.mkdir(parents=True, exist_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)
    failed = []
    for system in systems or DATASETS:
        try:
            fetch(system)
        except Exception as e:  # keep going so one bad dataset doesn't stop the rest
            print(f"{system}: FAILED: {e}", flush=True)
            failed.append(system)
    if failed:
        sys.exit(f"failed: {', '.join(failed)}")


if __name__ == "__main__":
    main(sys.argv[1:])
