"""Find identical units so one tagging decision can be applied to all of them.

Repeated equipment (four VAV boxes, three chillers) usually differs only by a token in
its point names: VAV_DMPR_S vs VAV_DMPR_E, CHL_POW_1 vs CHL_POW_2. Strip that token and
identical units have identical generic names. A unit only joins a group if every mapped
point also has the same profile shape as the representative's; otherwise it is tagged
on its own.
"""

UNIT_TOKEN = "{u}"


def unit_token(points: list[str]) -> tuple[int, str] | None:
    """The (position, token) shared by every point of a unit: last '_' part first, then first part."""
    parts = [p.split("_") for p in points]
    if not parts or any(len(x) < 2 for x in parts):
        return None
    for pos in (-1, 0):
        tokens = {x[pos] for x in parts}
        if len(tokens) == 1 and looks_like_id(token := tokens.pop()):
            return pos, token
    return None


def looks_like_id(token: str) -> bool:
    """Unit tokens are short (S, SA, E) or numbered (1, 02, AHU3); words like TEMP are not."""
    return len(token) <= 3 or any(ch.isdigit() for ch in token)


def generic_names(points: list[str]) -> dict[str, str] | None:
    """Map generic name -> point, e.g. VAV_DMPR_{u} -> VAV_DMPR_S. None if the unit has no shared token."""
    key = unit_token(points)
    if key is None:
        return None
    pos, _ = key
    out = {}
    for p in points:
        parts = p.split("_")
        parts[pos] = UNIT_TOKEN
        g = "_".join(parts)
        if g in out:
            return None
        out[g] = p
    return out


def groups(units: list[dict], kinds: dict[str, str]) -> list[dict]:
    """Group identical units. Each group: {"rep": unit, "siblings": [{"unit": unit, "mapping": {rep_point: point}}]}."""
    keyed: dict[tuple, list[tuple[dict, dict[str, str]]]] = {}
    out = []
    for u in sorted(units, key=lambda u: u["id"]):
        g = generic_names(u["points"])
        if g is None:
            out.append({"rep": u, "siblings": []})
        else:
            keyed.setdefault((u["type"], frozenset(g)), []).append((u, g))
    for members in keyed.values():
        (rep, rep_g), rest = members[0], members[1:]
        group = {"rep": rep, "siblings": []}
        for unit, g in rest:
            mapping = {rep_g[name]: g[name] for name in rep_g}
            if all(kinds.get(a) == kinds.get(b) for a, b in mapping.items()):
                group["siblings"].append({"unit": unit, "mapping": mapping})
            else:
                out.append({"rep": unit, "siblings": []})
        out.append(group)
    return out
