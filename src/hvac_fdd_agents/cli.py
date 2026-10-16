"""Command line. Every command the tool has is defined here.

  hvac-fdd-agents new-config TREND.csv --system MYBLDG     # blank points config to tag by hand
  hvac-fdd-agents tag TREND.csv --system MYBLDG             # phase 1: agents propose the points config
  hvac-fdd-agents score-tagging runs/PFPU/multi-.../points.yaml PFPU
  hvac-fdd-agents analyze TREND.csv --points PFPU          # phases 2+4 -> Site Snapshot in runs/
  hvac-fdd-agents chat                                      # ask about the newest Site Snapshot
  hvac-fdd-agents dashboard                                 # the same, in a browser

--points and --reference take a file, or a system name for config/points/<SYSTEM>.yaml.
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

import yaml

from hvac_fdd_agents import paths
from hvac_fdd_agents.data import points_config
from hvac_fdd_agents.scoring.tagging import score


def _points(arg: str) -> Path:
    """A points config file, or a system name for config/points/<SYSTEM>.yaml."""
    p = Path(arg)
    if p.exists() or p.suffix in (".yaml", ".yml"):
        return p
    return paths.points_config(arg)


def _new_run_dir(system: str, kind: str) -> Path:
    return paths.RUNS / system / f"{kind}-{datetime.now():%Y%m%d-%H%M%S}"


def interactive_checkpoint(equipment: dict, problems: list[str], proposal_file: Path) -> dict:
    """The Equipment Checkpoint: show proposed units, let the person approve, edit, or reject."""
    print("\n=== Equipment Checkpoint ===")
    for u in equipment["units"]:
        feed = f" <- {u['served_by']}" if u.get("served_by") else ""
        print(f"  {u['id']:<10} {u['type']:<22} {len(u['points']):>3} points{feed}")
    if equipment.get("notes"):
        print(f"\n  Agent notes: {equipment['notes']}")
    if problems:
        print("\n  Validator problems (must be fixed before approving):")
        for p in problems:
            print(f"   - {p}")
    print(f"\nFull proposal: {proposal_file}")
    while True:
        choice = input("[a]pprove, [e]dit the file then continue, [r]eject? ").strip().lower()
        if choice == "a":
            return {"approved": True, "equipment": None}
        if choice == "e":
            input(f"Edit {proposal_file}, save it, then press Enter... ")
            return {"approved": True, "equipment": yaml.safe_load(proposal_file.read_text(encoding="utf-8"))}
        if choice == "r":
            return {"approved": False, "equipment": None}


def cmd_tag(args) -> None:
    from hvac_fdd_agents.claude.client import LLM
    from hvac_fdd_agents.claude.credentials import ensure_api_key
    from hvac_fdd_agents.tagging.run import auto_approve, tag_trend_file

    ensure_api_key()
    out = Path(args.out) if args.out else _new_run_dir(args.system, args.mode)
    llm = LLM(model=args.model, effort=args.effort)
    checkpoint = auto_approve if args.auto_approve or args.mode == "single" else interactive_checkpoint
    points_file = tag_trend_file(Path(args.trend), args.system, llm, out, args.mode, checkpoint)
    record = json.loads((out / "run.json").read_text(encoding="utf-8"))
    print(f"\nWrote {points_file}")
    print(f"Review Queue: {record['review_items']} item(s) in {out / 'review_queue.yaml'}")
    print(f"{record['usage'] and sum(u['calls'] for u in record['usage'])} model calls, "
          f"~${record['total_cost_usd']:.2f}, agents {record['seconds']['agents']}s "
          f"(+{record['seconds']['waiting_for_human']}s at the checkpoint)")
    if args.reference:
        _report(points_file, _points(args.reference), out / "score.json")


def cmd_new_config(args) -> None:
    out = Path(args.out) if args.out else paths.points_config(args.system)
    descriptions = points_config.read_points_list(Path(args.points_list)) if args.points_list else None
    n = points_config.write_blank(Path(args.trend), args.system, out, descriptions)
    print(f"Wrote {out} ({n} points). Fill in units and tags by hand, or run `tag` to have the agents propose them.")


def cmd_analyze(args) -> None:
    from hvac_fdd_agents.site.analyze import analyze

    points_file = _points(args.points)
    cfg = points_config.load(points_file)
    out = Path(args.out) if args.out else _new_run_dir(cfg.system, "site")
    llm = None
    if args.tiebreak:
        from hvac_fdd_agents.claude.client import LLM
        from hvac_fdd_agents.claude.credentials import ensure_api_key
        ensure_api_key()
        llm = LLM(model=args.model, effort="medium")
    overrides = {k: v for k, v in {"faultbook": args.faultbook, "signature_model": args.signature_model}.items() if v}
    analyze(Path(args.trend), points_file, out, overrides, llm=llm, as_of=args.as_of,
            log=lambda m: print(f"  {m}..."))
    snap = json.loads((out / "snapshot.json").read_text(encoding="utf-8"))
    orders = json.loads((out / "work_orders.json").read_text(encoding="utf-8"))
    print(f"\n{snap['system']} as of {snap['as_of']} (data from {snap['data']['start'][:10]})")
    for unit, u in snap["units"].items():
        top = u["diagnosis"]["fault"] if u["diagnosis"] else ""
        print(f"  {unit:<10} {u['status']:<11} {u['pattern']:<11} {top:<28} data: {u['health']['status']}")
    for o in orders:
        print(f"  {o['id']} P{o['priority']} {o['unit']}: {o['fault'].replace('_', ' ')} -> {o['trade']} [{o['status']}]")
    if snap["model_usage"]["calls"]:
        print(f"  {snap['model_usage']['calls']} model call(s), ~${snap['model_usage']['est_cost_usd']:.2f}")
    print(f"\nSnapshot: {out}\nNext: hvac-fdd-agents dashboard --site {out}   (or: chat --site {out})")


def _pick_site(arg: str | None) -> Path:
    from hvac_fdd_agents.site.snapshot import find_snapshots

    if arg:
        return Path(arg)
    found = find_snapshots()
    if not found:
        raise SystemExit("No Site Snapshot found under runs/. Run `hvac-fdd-agents analyze` first.")
    return found[0]


def cmd_chat(args) -> None:
    import getpass

    from hvac_fdd_agents.chat.agent import Chat
    from hvac_fdd_agents.claude.credentials import ensure_api_key
    from hvac_fdd_agents.site.snapshot import Site

    ensure_api_key()
    site = Site(_pick_site(args.site))
    chat = Chat(site, model=args.model, effort=args.effort)
    me = getpass.getuser()
    print(f"{site.system}, snapshot as of {site.meta['as_of']} ({site.path})")
    print("Ask anything about the site. Commands: /queue, /approve WO-n [note], /reject WO-n [note], /cost, /quit\n")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not text:
            continue
        if text.startswith("/"):
            cmd, *rest = text.split(maxsplit=2)
            if cmd in ("/quit", "/exit"):
                return
            if cmd == "/cost":
                print(f"  {chat.usage.calls} calls, ~${chat.usage.cost_usd:.2f}")
            elif cmd == "/queue":
                pending = site.work_orders("pending_approval")
                print("\n\n".join(_render(o) for o in pending) if pending else "  Nothing waiting for approval.")
            elif cmd in ("/approve", "/reject") and rest:
                try:
                    o = site.decide(rest[0].upper(), cmd == "/approve", by=me, note=rest[1] if len(rest) > 1 else "")
                    print(f"  {o['id']} is now {o['status']}.")
                except ValueError as e:
                    print(f"  {e}")
            else:
                print("  Commands: /queue, /approve WO-n [note], /reject WO-n [note], /cost, /quit")
            continue
        print("assistant> ", end="", flush=True)
        for ev in chat.send(text):
            if ev.kind == "text":
                print(ev.text, end="", flush=True)
            elif ev.kind == "tool_call":
                args_ = ", ".join(f"{k}={v!r}" for k, v in ev.data.items())
                print(f"\n  [{ev.name}({args_})]", end="", flush=True)
            elif ev.kind == "tool_result" and ev.data.get("is_error"):
                print(f" -> {ev.text}", end="", flush=True)
            elif ev.kind == "notice":
                print(f"\n  ({ev.text})", end="")
        print("\n")


def _render(o: dict) -> str:
    return (f"  {o['id']}  P{o['priority']}  {o['unit']}  [{o['status']}, from {o['source']}]\n"
            f"  Likely problem: {o['fault'].replace('_', ' ')}\n  Send: {o['trade']}\n  Do: {o['action']}\n"
            f"  Why: {o['evidence']}")


def cmd_dashboard(args) -> None:
    import subprocess
    import sys

    from hvac_fdd_agents.paths import ROOT

    app = ROOT / "dashboard" / "app.py"
    if args.host not in ("localhost", "127.0.0.1"):
        print(f"WARNING: serving on {args.host}. The dashboard has no login: anyone who can reach this "
              "address can read the data, chat on your API key, and approve Work Orders.")
    cmd = [sys.executable, "-m", "streamlit", "run", str(app), "--server.port", str(args.port),
           "--server.address", args.host, "--browser.gatherUsageStats", "false"]
    if args.site:
        cmd += ["--", "--site", str(Path(args.site).resolve())]
    raise SystemExit(subprocess.call(cmd, cwd=ROOT))   # cwd: picks up .streamlit/config.toml


def cmd_score(args) -> None:
    _report(Path(args.agent), _points(args.reference), Path(args.agent).with_name("score.json"))


def _report(agent_file: Path, ref_file: Path, out_file: Path) -> None:
    result = score(points_config.load(agent_file), points_config.load(ref_file))
    print("\n" + result.summary())
    out_file.write_text(json.dumps({"unit_map": result.unit_map, "equipment": result.equipment,
                                    "selection": result.selection, "tags": result.tags,
                                    "mismatches": result.mismatches}, indent=2), encoding="utf-8")
    print(f"Details: {out_file}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="hvac-fdd-agents")
    sub = ap.add_subparsers(dest="command", required=True)

    t = sub.add_parser("tag", help="propose a Haystack points config for a Trend File")
    t.add_argument("trend", help="wide CSV: timestamp column, then one column per point")
    t.add_argument("--system", required=True, help="name for this site/system")
    t.add_argument("--mode", choices=["multi", "single"], default="multi")
    t.add_argument("--auto-approve", action="store_true", help="skip the Equipment Checkpoint (benchmark runs)")
    t.add_argument("--model", default="claude-opus-5-5")
    t.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default="medium")
    t.add_argument("--reference", help="Reference Mapping (file or system name) to score against when done")
    t.add_argument("--out", help="output directory (default runs/<system>/<mode>-<time>)")
    t.set_defaults(func=cmd_tag)

    n = sub.add_parser("new-config", help="write a blank points config for a Trend File, to tag by hand")
    n.add_argument("trend", help="wide CSV: timestamp column, then one column per point")
    n.add_argument("--system", required=True, help="name for this site/system")
    n.add_argument("--points-list", help="optional CSV with columns point,desc,units")
    n.add_argument("--out", help="default config/points/<SYSTEM>.yaml; never overwrites")
    n.set_defaults(func=cmd_new_config)

    a = sub.add_parser("analyze", help="build a Site Snapshot: data health, rules, Incidents, Work Orders")
    a.add_argument("trend")
    a.add_argument("--points", required=True, help="approved points config: a file, or a system name")
    a.add_argument("--as-of", help="YYYY-MM-DD: ignore data after this day (default: the last day in the file)")
    a.add_argument("--tiebreak", action="store_true", help="let the model decide close diagnoses (API calls)")
    a.add_argument("--model", default="claude-opus-5-5")
    a.add_argument("--faultbook")
    a.add_argument("--signature-model")
    a.add_argument("--out", help="output directory (default runs/<system>/site-<time>)")
    a.set_defaults(func=cmd_analyze)

    c = sub.add_parser("chat", help="ask questions about a Site Snapshot in the terminal")
    c.add_argument("--site", help="snapshot directory (default: the newest under runs/)")
    c.add_argument("--model", default="claude-opus-5-5")
    c.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default="medium")
    c.set_defaults(func=cmd_chat)

    b = sub.add_parser("dashboard", help="open the dashboard (with chat) in a browser")
    b.add_argument("--site", help="snapshot directory (default: pick in the app)")
    b.add_argument("--port", type=int, default=8501)
    b.add_argument("--host", default="localhost",
                   help="address to serve on (default: this computer only). There is no login; see the README")
    b.set_defaults(func=cmd_dashboard)

    s = sub.add_parser("score-tagging", help="score an agent points config against a Reference Mapping")
    s.add_argument("agent")
    s.add_argument("reference", help="Reference Mapping: a file, or a system name")
    s.set_defaults(func=cmd_score)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
