"""Where everything lives. Code gets file locations from here, never by building its own paths."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Hand-written settings (edit these).
CONFIG = ROOT / "config"
POINTS = CONFIG / "points"                       # one points config per system: <SYSTEM>.yaml
FAULTBOOK = CONFIG / "faultbook.yaml"            # which fault rules run, with what thresholds
ROLES = CONFIG / "haystack_roles.yaml"           # Haystack tags -> the point roles fault rules need
DISPATCH = CONFIG / "dispatch.yaml"              # Persistence and diagnosis caution
REMEDIATION = CONFIG / "remediation.yaml"        # fault -> trade, action, consequence
POLICY = CONFIG / "policy.yaml"                  # what an agent may do on its own
HEALTH = CONFIG / "health.yaml"                  # Data Health thresholds

# Learned from data (regenerate, don't edit).
MODELS = ROOT / "models"
SIGNATURE_MODEL = MODELS / "signature_model.yaml"

# Data in, results out.
DATA = ROOT / "data"
LBNL = DATA / "lbnl"                             # full LBNL data, from benchmark/setup/fetch.py
SAMPLE = DATA / "sample"
RUNS = ROOT / "runs"                             # everything the tools write

# The LBNL benchmark.
BENCHMARK = ROOT / "benchmark"
GROUND_TRUTH = BENCHMARK / "ground_truth"

# The files `analyze` reads; a caller can override any of them by key.
PIPELINE = {"faultbook": FAULTBOOK, "roles": ROLES, "dispatch": DISPATCH, "remediation": REMEDIATION,
            "policy": POLICY, "health": HEALTH, "signature_model": SIGNATURE_MODEL}


def points_config(system: str) -> Path:
    return POINTS / f"{system}.yaml"
