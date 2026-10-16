"""Read and write points configs (the Reference Mapping and agent output share one format)."""

import csv
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from hvac_fdd_agents.data.trend import read_header

FEED_KEYS = ("ahuRef", "plantRef")
AIR_HANDLERS = {"ahu", "ahu-dualDuct", "rtu"}


@dataclass
class Unit:
    type: str
    feeds: str | None = None          # the unit that serves this one (ahuRef or plantRef)


@dataclass
class Point:
    unit: str | None = None
    tags: list[str] = field(default_factory=list)
    exclude: str | None = None
    review: str | None = None

    @property
    def kept(self) -> bool:
        return self.exclude is None


@dataclass
class PointsConfig:
    system: str
    units: dict[str, Unit]
    points: dict[str, Point]


def load(path: Path) -> PointsConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    units = {uid: Unit(type=u["type"], feeds=next((u[k] for k in FEED_KEYS if k in u), None))
             for uid, u in (raw.get("units") or {}).items()}
    points = {name: Point(unit=p.get("unit"), tags=list(p.get("tags") or []),
                          exclude=p.get("exclude"), review=p.get("review"))
              for name, p in (raw.get("points") or {}).items()}
    return PointsConfig(system=raw["system"], units=units, points=points)


def write(path: Path, cfg: PointsConfig, header: list[str], timestamp: dict | None = None) -> None:
    lines = [f"# {h}" for h in header]
    lines.append(f"system: {cfg.system}")
    if timestamp:
        lines.append(f'timestamp: {{column: "{timestamp["column"]}", format: "{timestamp["format"]}"}}')
    lines.append("units:")
    for uid, u in cfg.units.items():
        feed = ""
        if u.feeds:
            key = "ahuRef" if cfg.units.get(u.feeds, Unit("")).type in AIR_HANDLERS else "plantRef"
            feed = f", {key}: {u.feeds}"
        lines.append(f"  {uid}: {{type: {u.type}{feed}}}")
    lines.append("points:")
    for name, p in cfg.points.items():
        lines.append(f"  {_q(name)}:")
        if p.unit:
            lines.append(f"    unit: {p.unit}")
        if p.kept:
            lines.append(f"    tags: [{', '.join(p.tags)}]")
        else:
            lines.append(f"    exclude: {_q(p.exclude)}")
        if p.review:
            lines.append(f"    review: {_q(p.review)}")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def read_points_list(path: Path) -> dict[str, tuple[str, str]]:
    """A BMS points list: CSV with columns point,desc,units -> {point: (desc, units)}."""
    with Path(path).open(encoding="utf-8", newline="") as f:
        return {r["point"]: (r.get("desc", ""), r.get("units", "")) for r in csv.DictReader(f)}


def write_blank(trend: Path, system: str, out: Path, descriptions: dict[str, tuple[str, str]] | None = None) -> int:
    """A points config listing every point of a Trend File with empty unit and tags, ready to tag by hand.

    The filled-in file is the Reference Mapping. Never overwrites an existing file. Returns the point count.
    """
    if out.exists():
        raise FileExistsError(f"{out} already exists; not overwriting a config you may have edited")
    ts_col, points, ts_fmt = read_header(trend)
    descriptions = descriptions or {}
    lines = [
        f"# Reference Mapping for {system}: the hand-tagged answer key. Fill it in by hand.",
        "# units: one entry per piece of equipment (type, and ahuRef for terminals).",
        "# points: give each kept point a unit and Haystack tags, or set exclude: <reason>",
        "# for points not worth trending.",
        f"system: {system}",
        f'timestamp: {{column: "{ts_col}", format: "{ts_fmt}"}}',
        "units: {}",
        "points:",
    ]
    for point in points:
        lines.append(f'  "{point}":')
        if point in descriptions:
            desc, units = descriptions[point]
            lines += [f'    desc: "{desc}"', f'    units: "{units}"']
        lines += ["    unit:", "    tags: []"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(points)
