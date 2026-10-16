"""Fit the Fault Signature model on the LBNL VAV cases and save it for `hvac-fdd-agents analyze`.

Usage: python benchmark/train_signature_model.py [SYSTEM ...]   (default: PFPU SFPU)

Needs the phase 2 Candidate Windows (benchmark/phase2_run_rules.py). Trains on every
case of the given systems; the held-out numbers in docs/results/phase4-dispatch-dev.md
are the estimate of how it does on cases it hasn't seen. Writes models/signature_model.yaml.
"""

import sys
from collections import Counter

import phase4_dispatch as b

from hvac_fdd_agents import paths
from hvac_fdd_agents.dispatch import diagnose as dx
from hvac_fdd_agents.dispatch import incidents as inc


def main(systems: list[str]) -> None:
    dcfg = inc.load_config(paths.DISPATCH)
    rows = [r for s in systems for r in b.build_all(s, dcfg, "faultbook", peer_adjust=True)]
    examples = [(r["incident"].signature(dcfg), r["label"]) for r in rows if r["incident"].dispatchable(dcfg)]
    model = dx.SignatureModel.fit(examples)
    out = paths.SIGNATURE_MODEL
    counts = Counter(label for _, label in examples)
    model.save(out, [
        "Fault Signature model: P(rule persists | fault) and fault priors, naive Bayes (ADR-0007).",
        f"Trained by benchmark/train_signature_model.py on {', '.join(systems)} "
        f"({len(examples)} dispatchable Incidents at month ends, Fault Book v1).",
        "It only knows the fault types below; 'none' is noise learned from healthy units.",
        "Examples per class: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())),
    ])
    print(f"Wrote {out}: {len(model.prior)} classes, {len(model.rules)} rules, {len(examples)} examples")


if __name__ == "__main__":
    main(sys.argv[1:] or ["PFPU", "SFPU"])
