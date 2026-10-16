"""Does the model pick the right fault more often than the signature model? (Variant D)

Usage: python benchmark/phase4_tiebreak.py [--max-calls 40]

For each faulted case the held-out-case signature model dispatched on, take the first
dispatch on the faulted box, give the model the top-3 candidates (non-'none') plus the
evidence pack, and compare its choice with the signature model's top-1 on the same set.
Costs money: one model call per case.
"""

import argparse
import json

import pandas as pd
import phase4_dispatch as b

from hvac_fdd_agents import paths
from hvac_fdd_agents.claude.client import LLM, Usage
from hvac_fdd_agents.claude.credentials import ensure_api_key
from hvac_fdd_agents.data import points_config, trend
from hvac_fdd_agents.dispatch import diagnose as dx
from hvac_fdd_agents.dispatch import incidents as inc

NONE_THRESHOLD = 0.5


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-calls", type=int, default=40)
    args = ap.parse_args()
    ensure_api_key()
    dcfg = inc.load_config(paths.DISPATCH)
    rows = [r for s in ("PFPU", "SFPU") for r in b.build_all(s, dcfg, "faultbook", True)]
    cases = sorted({(r["system"], r["case"]) for r in rows if r["faulted_unit"]})

    sample = []
    for held in cases:
        train = [r for r in rows if (r["system"], r["case"]) != held and r["incident"].dispatchable(dcfg)]
        model = dx.SignatureModel.fit([(r["incident"].signature(dcfg), r["label"]) for r in train])
        mine = sorted((r for r in rows if (r["system"], r["case"]) == held and r["unit"] == r["faulted_unit"]
                       and r["incident"].dispatchable(dcfg)), key=lambda r: r["as_of"])
        for r in mine:
            ranking = model.rank(r["incident"].signature(dcfg))
            if dict(ranking).get(dx.NONE, 0) < NONE_THRESHOLD:
                sample.append((r, [x for x in ranking if x[0] != dx.NONE]))
                break
    sample = sample[:args.max_calls]
    print(f"{len(sample)} cases to diagnose")

    llm, usage, results = LLM(effort="medium"), Usage(), []
    for i, (r, ranking) in enumerate(sample, 1):
        system = r["system"]
        cfg = points_config.load(paths.points_config(system))
        peers = b.peers_for(cfg)
        con, _ = trend.load(paths.LBNL / system / r["case"])
        _, op_point = b.operating_minutes(paths.LBNL / system / r["case"], cfg)
        end = r["as_of"] + pd.Timedelta(days=1)
        evidence = dx.evidence_pack(con, cfg, r["unit"], peers.get(r["unit"], []),
                                    end - pd.Timedelta(days=dcfg.lookback_days), end, op_point)
        answer = dx.tiebreak(llm, r["unit"], r["incident"].persistent(dcfg), ranking, evidence, usage)
        results.append({"system": system, "case": r["case"], "as_of": str(r["as_of"].date()), "truth": r["label"],
                        "signature_top1": ranking[0][0], "in_top3": r["label"] in [f for f, _ in ranking[:3]],
                        "model_choice": answer.fault, "confidence": answer.confidence,
                        "explanation": answer.explanation})
        print(f"  {i}/{len(sample)} {r['case']}: truth={r['label']} signature={ranking[0][0]} model={answer.fault}",
              flush=True)

    n = len(results)
    summary = {
        "cases": n,
        "signature_top1_correct": round(sum(x["signature_top1"] == x["truth"] for x in results) / n, 3),
        "model_correct": round(sum(x["model_choice"] == x["truth"] for x in results) / n, 3),
        "truth_in_top3": round(sum(x["in_top3"] for x in results) / n, 3),
        "model_said_none": sum(x["model_choice"] == dx.NONE for x in results),
        **usage.as_dict(),
    }
    out = paths.RUNS / "dispatch" / "tiebreak_eval"
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps({"summary": summary, "cases": results}, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
