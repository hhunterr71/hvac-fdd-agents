"""Fault Book edits: what the agent (or the plain tuner) may change, checked by code (ADR-0009).

An edit sets one rule on one equipment kind: disabled, or enabled with parameter overrides.
Every parameter must be one the rule defines, and within the min/max open-fdd publishes
for it. Anything else is rejected with a reason, never silently clamped.
"""

import copy
from dataclasses import dataclass
from pathlib import Path

from hvac_fdd_agents.rules.faultbook import FaultBook


@dataclass(frozen=True)
class Edit:
    kind: str
    rule: str
    enabled: bool = True
    params: tuple = ()            # ((key, value), ...): overrides from the rule's defaults; () = defaults
    reason: str = ""

    @property
    def overrides(self) -> dict:
        return dict(self.params)

    def describe(self) -> str:
        what = "disable" if not self.enabled else (
            ", ".join(f"{k}={v:g}" for k, v in self.params) if self.params else "reset to defaults")
        return f"{self.kind}/{self.rule}: {what}"


def make(kind: str, rule: str, enabled: bool = True, params: dict | None = None, reason: str = "") -> Edit:
    return Edit(kind, rule, enabled, tuple(sorted((params or {}).items())), reason)


def rule_params(rule: str) -> list[dict]:
    """The parameters a rule defines, with defaults and legal bounds (from open-fdd)."""
    from open_fdd.rules import RULES_BY_ID
    r = RULES_BY_ID.get(rule)
    if r is None:
        return []
    return [{"key": p.key, "label": p.label, "unit": p.unit, "default": p.default, "min": p.min, "max": p.max,
             "step": p.step, "fewer_alarms": p.direction or ""} for p in r.params]


def validate(book: FaultBook, edit: Edit) -> list[str]:
    """Problems with an edit (empty = legal)."""
    if edit.kind not in book.rules:
        return [f"no equipment kind {edit.kind!r} in the Fault Book; kinds: {', '.join(book.rules)}"]
    if edit.rule not in book.rules[edit.kind]:
        return [f"{edit.rule} isn't a rule for {edit.kind}"]
    if not edit.enabled:
        return [] if not edit.params else ["a disabled rule takes no parameters"]
    defined = {p["key"]: p for p in rule_params(edit.rule)}
    problems = []
    for key, value in edit.params:
        p = defined.get(key)
        if p is None:
            problems.append(f"{edit.rule} has no parameter {key!r}; it has {', '.join(defined) or 'none'}")
        elif not isinstance(value, (int, float)) or isinstance(value, bool):
            problems.append(f"{edit.rule}.{key} must be a number")
        elif not p["min"] <= value <= p["max"]:
            problems.append(f"{edit.rule}.{key}={value:g} is outside {p['min']:g}..{p['max']:g}")
    return problems


def apply(book: FaultBook, edits: list[Edit]) -> FaultBook:
    """A new Fault Book with the edits applied (each must be valid; later edits win)."""
    rules = copy.deepcopy(book.rules)
    for e in edits:
        problems = validate(book, e)
        if problems:
            raise ValueError("; ".join(problems))
        rules[e.kind][e.rule] = {"enabled": False} if not e.enabled else e.overrides
    return FaultBook(book.version, book.source, rules)


def diff(old: FaultBook, new: FaultBook) -> list[Edit]:
    """The edits that turn `old` into `new`."""
    out = []
    for kind, rules in new.rules.items():
        for rule, p in rules.items():
            if p != old.rules.get(kind, {}).get(rule):
                enabled = p.get("enabled", True)
                out.append(make(kind, rule, enabled, {k: v for k, v in p.items() if k != "enabled"}))
    return out


def write(book: FaultBook, path: Path, header: list[str]) -> None:
    """Write a Fault Book YAML in the same layout as config/faultbook.yaml, with rule titles as comments."""
    from open_fdd.rules import RULES_BY_ID
    lines = [f"# {h}" for h in header]
    lines += [f"version: {book.version}", f'source: "{book.source}"', "equipment:"]
    for kind, rules in book.rules.items():
        lines.append(f"  {kind}:")
        for rule, p in rules.items():
            body = ", ".join(f"{k}: {str(v).lower() if isinstance(v, bool) else v}" for k, v in p.items())
            title = getattr(RULES_BY_ID.get(rule), "title", "")
            lines.append(f"    {rule}: {{{body}}}" + (f"  # {title}" if title else ""))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
