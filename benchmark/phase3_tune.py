"""Phase 3 benchmark: tune the Fault Book on one system, judge it on the other (ADR-0009).

Usage:
  python benchmark/phase3_tune.py --check                    # backtester reproduces phase 4's numbers?
  python benchmark/phase3_tune.py --tune PFPU --eval SFPU --budget 25 [--methods plain agent]

For each method (the defaults, the plain tuner, the agent): tune on --tune using Work Order
Outcomes from its labels, fit the signature model there, then dispatch on --eval, which
the method never got feedback from. Each tuned Fault Book is saved as a proposal for review
on the dashboard. The agent makes API calls (budget-capped).
"""

import argparse
import json
import time
from datetime import datetime

import lbnl

from hvac_fdd_agents import paths
from hvac_fdd_agents.data import points_config
from hvac_fdd_agents.dispatch import incidents as inc
from hvac_fdd_agents.dispatch import policy as pol
from hvac_fdd_agents.dispatch import workorder as wo
from hvac_fdd_agents.rules import faultbook
from hvac_fdd_agents.tuning import plain, proposals
from hvac_fdd_agents.tuning.backtest import Backtester, score
from hvac_fdd_agents.tuning.cache import RuleCache

CACHE = paths.RUNS / "tuning" / "cache"


def backtester(system: str) -> Backtester:
    cache = RuleCache(CACHE, system, paths.points_config(system), paths.ROLES, lbnl.cases(system))
    return Backtester(cache, points_config.load(paths.points_config(system)), lbnl.cases(system),
                      inc.load_config(paths.DISPATCH), wo.load_remediation(paths.REMEDIATION), pol.load(paths.POLICY))


def check() -> None:
    """Leave-one-case-out over PFPU+SFPU at the defaults must match docs/results/phase4-dispatch-dev.md."""
    book = faultbook.load(paths.FAULTBOOK)
    bts = [backtester(s) for s in ("PFPU", "SFPU")]
    rows = [r for bt in bts for r in bt.rows(book)]
    dispatches = []
    for case in sorted({r.case for r in rows}):
        model = bts[0].fit([r for r in rows if r.case != case])
        dispatches += bts[0].dispatch([r for r in rows if r.case == case], model)
    m = score(rows, dispatches, bts[0].remediation, bts[0].ahus | bts[1].ahus)
    print(json.dumps(m, indent=2))
    ok = m["detection_rate"] == 0.667 and m["unnecessary_terminal_per_100_unit_months"] == 15.5 \
        and m["unnecessary_ahu"] == 0
    print("MATCHES phase 4 (66.7% caught, 15.5 per 100, 0 AHU)" if ok else "DOES NOT MATCH phase 4")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--tune", default="PFPU")
    ap.add_argument("--eval", default="SFPU")
    ap.add_argument("--budget", type=int, default=25, help="backtests per method, including the starting one")
    ap.add_argument("--methods", nargs="+", default=["plain", "agent"], choices=["plain", "agent"])
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--max-cost", type=float, default=5.0, help="agent spending cap, USD")
    args = ap.parse_args()
    if args.check:
        return check()

    book = faultbook.load(paths.FAULTBOOK)
    tune_bt, eval_bt = backtester(args.tune), backtester(args.eval)
    log = lambda m: print(m, flush=True)  # noqa: E731
    log(f"defaults: tuning on {args.tune}, judging on {args.eval}")
    base_tune = tune_bt.run(book, log=log)
    base_eval = eval_bt.run(book, model=base_tune.model, log=log)
    results = {"tuned_on": args.tune, "evaluated_on": args.eval, "budget": args.budget,
               "defaults": {"tuning": base_tune.metrics, "evaluation": base_eval.metrics}}

    for method in args.methods:
        t0 = time.time()
        extra, summary, reasons = {}, "", {}
        if method == "plain":
            tuned, history = plain.tune(tune_bt, book, args.budget, log=log)
            extra["history"] = history
            summary = "Greedy search: disable or move one rule at a time, ranked by no-fault-found dispatches."
        else:
            from hvac_fdd_agents.claude.credentials import ensure_api_key
            from hvac_fdd_agents.tuning import agent
            ensure_api_key()
            session = agent.Session(tune_bt, book, args.budget, log=log)
            usage = agent.run(session, args.tune, effort=args.effort, max_cost_usd=args.max_cost)
            tuned, summary = session.book, session.summary
            reasons = {(e.kind, e.rule): e.reason for e in session.kept}
            extra["usage"] = usage.as_dict()
            extra["backtests_used"] = session.used
        tuned_tune = tune_bt.run(tuned, log=log)
        tuned_eval = eval_bt.run(tuned, model=tuned_tune.model, log=log)
        folder = proposals.save(method, args.tune, args.eval, book, tuned, reasons,
                                {"before": base_tune.metrics, "after": tuned_tune.metrics},
                                {"before": base_eval.metrics, "after": tuned_eval.metrics}, summary, extra)
        results[method] = {"tuning": tuned_tune.metrics, "evaluation": tuned_eval.metrics,
                           "minutes": round((time.time() - t0) / 60, 1), "proposal": folder.name, **extra}
        log(f"{method}: tuning objective {base_tune.metrics['objective']} -> {tuned_tune.metrics['objective']}, "
            f"held-out {base_eval.metrics['objective']} -> {tuned_eval.metrics['objective']}")

    out = paths.RUNS / "tuning" / f"{datetime.now():%Y%m%d-%H%M%S}-{args.tune}_to_{args.eval}.json"
    out.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    log("\n| | caught (held-out) | false per 100 (held-out) | objective (held-out) | objective (tuning) |")
    log("|---|---|---|---|---|")
    for name in ["defaults"] + args.methods:
        e, t = results[name]["evaluation"], results[name]["tuning"]
        log(f"| {name} | {e['detection_rate']:.0%} | {e['unnecessary_terminal_per_100_unit_months']} | "
            f"{e['objective']} | {t['objective']} |")
    log(f"\nDetails: {out}")


if __name__ == "__main__":
    main()
