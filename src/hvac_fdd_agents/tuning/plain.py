"""The plain tuner: a deterministic search the agent has to beat (ADR-0009).

Greedy, with the same backtest budget as the agent:
  1. rank rules by blame: dispatches they drove that ended "no fault found", then hours
     they persisted on healthy units;
  2. for each, in order: try disabling it; else try moving each parameter halfway toward
     its lower and upper bound; keep the single best change if it raises the objective;
  3. stop when the budget runs out.
"""

from hvac_fdd_agents.rules.faultbook import FaultBook
from hvac_fdd_agents.tuning import edits as ed
from hvac_fdd_agents.tuning.backtest import Backtester, Result


def blame(result: Result) -> list[tuple[str, str]]:
    ranked = sorted(result.rules, key=lambda r: (-r["dispatches"].get("no_fault_found", 0),
                                                 -r["persistent_on_healthy_unit_months"], -r["fault_hours"]))
    return [(r["kind"], r["rule"]) for r in ranked
            if r["dispatches"].get("no_fault_found", 0) or r["persistent_on_healthy_unit_months"]]


def candidates(book: FaultBook, kind: str, rule: str) -> list[ed.Edit]:
    out = [ed.make(kind, rule, enabled=False)]
    current = book.rules[kind][rule]
    for p in ed.rule_params(rule):
        if p["min"] == p["max"] or p["unit"] == "bool":
            continue
        value = current.get(p["key"], p["default"])
        for target in (p["min"], p["max"]):
            new = round((value + target) / 2, 4)
            if new != value:
                out.append(ed.make(kind, rule, params={**{k: v for k, v in current.items() if k != "enabled"},
                                                        p["key"]: new}))
    return out


def tune(bt: Backtester, book: FaultBook, budget: int, log=print) -> tuple[FaultBook, list[dict]]:
    """Returns the tuned Fault Book and a log of every backtest (edit, objective, kept)."""
    best = bt.run(book, log=lambda m: None)
    used, history = 1, [{"edit": "defaults", "objective": best.metrics["objective"], "kept": True}]
    log(f"plain tuner: start objective {best.metrics['objective']}")
    for kind, rule in blame(best):
        trial_best, trial_edit, trial_i = None, None, None
        for e in candidates(book, kind, rule):
            if used >= budget:
                break
            r = bt.run(ed.apply(book, [e]), log=lambda m: None)
            used += 1
            history.append({"edit": e.describe(), "objective": r.metrics["objective"], "kept": False})
            if r.metrics["objective"] > (trial_best or best).metrics["objective"]:
                trial_best, trial_edit, trial_i = r, e, len(history) - 1
        if trial_edit:
            book, best = ed.apply(book, [trial_edit]), trial_best
            history[trial_i]["kept"] = True
            log(f"  kept {trial_edit.describe()}: objective {best.metrics['objective']} ({used}/{budget} backtests)")
        if used >= budget:
            break
    return book, history
