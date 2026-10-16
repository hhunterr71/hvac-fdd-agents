"""Phase 4 benchmark: Candidate Windows -> Incidents -> diagnosis -> Work Orders, scored.

Usage: python benchmark/phase4_dispatch.py --train PFPU --test SFPU [--llm --max-llm-calls 40]

Reuses Candidate Windows from phase2_run_rules.py (runs/fdd/<faultbook>/<SYSTEM>/windows).
Dispatch is decided at 12 month-end dates per case. The signature model is trained on
--train and scored on --test, a different box type, so nothing is learned on test cases.

Variants scored (each adds one piece):
  A persistence only        raw rule minutes, every persistent Incident dispatched
  B + peer comparison       minutes above identical siblings' median
  C + signature model       learned fault ranking, with a "none" class that can suppress
  D + model tiebreak        (--llm) the model decides close calls from an evidence pack
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

import pandas as pd
from lbnl import load_catalog

from hvac_fdd_agents import paths
from hvac_fdd_agents.data import points_config, trend
from hvac_fdd_agents.dispatch import diagnose as dx
from hvac_fdd_agents.dispatch import incidents as inc
from hvac_fdd_agents.dispatch import policy as pol
from hvac_fdd_agents.dispatch import workorder as wo

MARGIN = dx.MARGIN


def month_ends(year: int = 2018) -> list[pd.Timestamp]:
    return [pd.Timestamp(year, m, 1) + pd.offsets.MonthEnd(0) for m in range(1, 13)]


peers_for = inc.peers_for


def operating_minutes(trend_file: Path, cfg) -> tuple[pd.Series, str | None]:
    """Daily minutes the air handler fan runs (the 'operating hours' for Persistence)."""
    return inc.operating_minutes(trend.load(trend_file)[0], cfg)


def cached_operating(system: str, file: str, cfg) -> pd.Series:
    cache = paths.RUNS / "dispatch" / "cache" / system / (file.removesuffix(".csv") + ".operating.csv")
    if cache.exists():
        s = pd.read_csv(cache, index_col=0, parse_dates=True).iloc[:, 0]
        s.index = pd.DatetimeIndex(s.index)
        return s
    op, _ = operating_minutes(paths.LBNL / system / file, cfg)
    cache.parent.mkdir(parents=True, exist_ok=True)
    op.rename("minutes").to_csv(cache)
    return op


def case_incidents(system: str, case: dict, cfg, peers, dcfg, book: str, peer_adjust: bool) -> list[dict]:
    wf = pd.read_csv(paths.RUNS / "fdd" / book / system / "windows" / (case["file"].removesuffix(".csv") + ".csv.gz"),
                     parse_dates=["start", "end"])
    daily = inc.daily_minutes(wf)
    excess = inc.peer_adjust(daily, peers, dcfg.peer_baseline) if peer_adjust else daily.rename(columns={"minutes": "excess"})
    op = cached_operating(system, case["file"], cfg)
    out = []
    for as_of in month_ends():
        for unit, incident in inc.incidents_as_of(excess, as_of, list(cfg.units), op, dcfg).items():
            out.append({"system": system, "case": case["file"], "unit": unit, "as_of": as_of, "incident": incident,
                        "label": case["component"] + "_" + case["mode"] if case["fault_free"] == "False"
                        and unit == case["unit"] else dx.NONE,
                        "faulted_unit": case["unit"] or None})
    return out


def build_all(system: str, dcfg, book: str, peer_adjust: bool) -> list[dict]:
    cfg = points_config.load(paths.points_config(system))
    peers = peers_for(cfg)
    rows = []
    cases = [r for r in load_catalog() if r["system"] == system]
    for i, case in enumerate(cases, 1):
        rows += case_incidents(system, case, cfg, peers, dcfg, book, peer_adjust)
        print(f"  {system}: incidents for {i}/{len(cases)} cases", end="\r", flush=True)
    print()
    return rows


def score(rows: list[dict], orders: dict[tuple, wo.WorkOrder | None], dcfg, remediation) -> dict:
    by_case = defaultdict(list)
    for r in rows:
        by_case[r["case"]].append(r)
    detected, faulted_cases, unnecessary, twin_unnecessary, ahu_unnecessary = 0, 0, 0, 0, 0
    terminal_months = 0
    top1 = right_fix = top3 = diag_n = 0
    unit_months = 0
    for case, rs in by_case.items():
        faulted = rs[0]["faulted_unit"]
        if faulted:
            faulted_cases += 1
            hits = [r for r in rs if r["unit"] == faulted and orders.get(_k(r))]
            detected += bool(hits)
            for r in hits:
                o, truth = orders[_k(r)], r["label"]
                if o.fault == "unknown":           # variants without diagnosis
                    continue
                diag_n += 1
                top1 += o.fault == truth
                t = remediation["faults"].get(truth, {})
                right_fix += (o.trade, o.action) == (t.get("trade"), t.get("action"))
                top3 += truth in [f for f, _ in r.get("ranking", [])[:3]] if r.get("ranking") else o.fault == truth
        for r in rs:
            if r["unit"] != faulted:
                unit_months += 1
                is_ahu = r["unit"].startswith("AHU")
                terminal_months += not is_ahu
                if orders.get(_k(r)):
                    unnecessary += 1
                    twin_unnecessary += not faulted
                    ahu_unnecessary += is_ahu
    faulted_unit_dispatches = sum(1 for case, rs in by_case.items() for r in rs
                                  if r["faulted_unit"] and r["unit"] == r["faulted_unit"] and orders.get(_k(r)))
    return {
        "faulted_cases": faulted_cases,
        "detected_cases": detected,
        "detection_rate": round(detected / faulted_cases, 3) if faulted_cases else None,
        "diagnosis_top1": round(top1 / diag_n, 3) if diag_n else None,
        "diagnosis_top3": round(top3 / diag_n, 3) if diag_n else None,
        "right_fix": round(right_fix / diag_n, 3) if diag_n else None,
        "faulted_unit_dispatches": faulted_unit_dispatches,
        "unnecessary_dispatches": unnecessary,
        "unnecessary_per_100_unit_months": round(100 * unnecessary / unit_months, 1) if unit_months else None,
        "unnecessary_on_fault_free_twin": twin_unnecessary,
        "unnecessary_ahu": ahu_unnecessary,
        "unnecessary_terminal_per_100_unit_months":
            round(100 * (unnecessary - ahu_unnecessary) / terminal_months, 1) if terminal_months else None,
    }


def _k(r) -> tuple:
    return (r.get("system"), r["case"], r["unit"], r["as_of"])


def run_variant(name, train_rows, test_rows, dcfg, remediation, policy, use_model, tiebreaker=None):
    model = None
    if use_model:
        examples = [(r["incident"].signature(dcfg), r["label"]) for r in train_rows if r["incident"].dispatchable(dcfg)]
        model = dx.SignatureModel.fit(examples)
    orders = {}
    for r in test_rows:
        incident = r["incident"]
        if not incident.dispatchable(dcfg):
            continue
        if model:
            tb = (lambda ranking, r=r: tiebreaker(r, ranking)) if tiebreaker else None
            d = dx.diagnose(model, incident.signature(dcfg), MARGIN, tb, dcfg.none_threshold)
            r["ranking"] = d.ranking
        else:
            d = dx.Diagnosis(fault="unknown", probability=0.0, ranking=[], method="none")
        order = wo.build(incident, d, dcfg, remediation, policy) if model else _undiagnosed(incident, dcfg)
        orders[_k(r)] = order
    return score(test_rows, orders, dcfg, remediation), orders


def run_held_out_cases(rows: list[dict], dcfg, remediation, policy) -> tuple[dict, dict]:
    """Signature model trained on every other case (both systems), tested on the held-out case."""
    orders = {}
    cases = sorted({(r["system"], r["case"]) for r in rows})
    for i, held in enumerate(cases, 1):
        train = [r for r in rows if (r["system"], r["case"]) != held]
        test = [r for r in rows if (r["system"], r["case"]) == held]
        model = dx.SignatureModel.fit([(r["incident"].signature(dcfg), r["label"]) for r in train
                                       if r["incident"].dispatchable(dcfg)])
        for r in test:
            if r["incident"].dispatchable(dcfg):
                d = dx.diagnose(model, r["incident"].signature(dcfg), MARGIN, none_threshold=dcfg.none_threshold)
                r["ranking"] = d.ranking
                orders[_k(r)] = wo.build(r["incident"], d, dcfg, remediation, policy)
        print(f"  held-out cases: {i}/{len(cases)}", end="\r", flush=True)
    print()
    return score(rows, orders, dcfg, remediation), orders


def _undiagnosed(incident, dcfg) -> wo.WorkOrder:
    return wo.WorkOrder(incident.unit, str(incident.as_of.date()), 4, "unknown", "", "", "", incident.pattern(dcfg),
                        incident.magnitude(dcfg), "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train")
    ap.add_argument("--test")
    ap.add_argument("--held-out-cases", nargs="+", metavar="SYSTEM",
                    help="instead of --train/--test: leave-one-case-out over these systems")
    ap.add_argument("--faultbook", default="faultbook")
    ap.add_argument("--llm", action="store_true", help="variant D: model tiebreak on close calls (costs money)")
    ap.add_argument("--max-llm-calls", type=int, default=40)
    args = ap.parse_args()

    dcfg = inc.load_config(paths.DISPATCH)
    remediation = wo.load_remediation(paths.REMEDIATION)
    policy = pol.load(paths.POLICY)
    results = {}
    if args.held_out_cases:
        out = paths.RUNS / "dispatch" / ("held_out_cases_" + "_".join(args.held_out_cases))
        out.mkdir(parents=True, exist_ok=True)
        rows = [r for s_ in args.held_out_cases for r in build_all(s_, dcfg, args.faultbook, peer_adjust=True)]
        results["B_plus_peer_comparison"], _ = run_variant("B", [], rows, dcfg, remediation, policy, use_model=False)
        results["C_signature_model_held_out_cases"], orders = run_held_out_cases(rows, dcfg, remediation, policy)
        (out / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        sample = [o for o in orders.values() if o][:8]
        (out / "sample_work_orders.txt").write_text("\n\n".join(o.render() for o in sample), encoding="utf-8")
        print(json.dumps(results, indent=2))
        return
    out = paths.RUNS / "dispatch" / f"{args.train}_to_{args.test}"
    out.mkdir(parents=True, exist_ok=True)

    print("Variant A (no peer comparison)")
    test_raw = build_all(args.test, dcfg, args.faultbook, peer_adjust=False)
    results["A_persistence_only"], _ = run_variant("A", [], test_raw, dcfg, remediation, policy, use_model=False)

    print("Variants B-D (peer comparison)")
    train = build_all(args.train, dcfg, args.faultbook, peer_adjust=True)
    test = build_all(args.test, dcfg, args.faultbook, peer_adjust=True)
    results["B_plus_peer_comparison"], _ = run_variant("B", train, test, dcfg, remediation, policy, use_model=False)
    results["C_plus_signature_model"], orders_c = run_variant("C", train, test, dcfg, remediation, policy, use_model=True)

    if args.llm:
        from hvac_fdd_agents.claude.client import LLM, Usage
        from hvac_fdd_agents.claude.credentials import ensure_api_key
        ensure_api_key()
        llm, usage = LLM(effort="medium"), Usage()
        cfg = points_config.load(paths.points_config(args.test))
        peers = peers_for(cfg)
        cons, calls = {}, {"n": 0}

        def tiebreaker(r, ranking):
            if calls["n"] >= args.max_llm_calls:
                return dx.TiebreakAnswer(fault=ranking[0][0], confidence="low", explanation="")
            calls["n"] += 1
            if r["case"] not in cons:
                cons[r["case"]] = trend.load(paths.LBNL / args.test / r["case"])[0]
            _, op_point = operating_minutes(paths.LBNL / args.test / r["case"], cfg)
            end = r["as_of"] + pd.Timedelta(days=1)
            ev = dx.evidence_pack(cons[r["case"]], cfg, r["unit"], peers.get(r["unit"], []),
                                  end - pd.Timedelta(days=dcfg.lookback_days), end, op_point)
            return dx.tiebreak(llm, r["unit"], r["incident"].persistent(dcfg), ranking, ev, usage)

        results["D_plus_model_tiebreak"], orders_d = run_variant("D", train, test, dcfg, remediation, policy,
                                                                 use_model=True, tiebreaker=tiebreaker)
        results["D_plus_model_tiebreak"]["model_calls"] = calls["n"]
        results["D_plus_model_tiebreak"]["est_cost_usd"] = round(usage.cost_usd, 2)
        orders_c = orders_d

    (out / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    sample = [o for o in orders_c.values() if o][:5]
    (out / "sample_work_orders.txt").write_text("\n\n".join(o.render() for o in sample), encoding="utf-8")
    print(json.dumps(results, indent=2))
    print(f"Details: {out}")


if __name__ == "__main__":
    main()
