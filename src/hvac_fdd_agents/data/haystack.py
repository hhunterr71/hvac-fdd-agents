"""The Project Haystack vocabulary the tagging agents may use, and a validator for it.

Tags follow flat Haystack point style (e.g. `discharge air temp sensor`), with the
Haystack 4 conjuncts for water (`hot-water`, `chilled-water`, `condenser-water`).
The validator is deterministic: agents propose tags, code decides if they're legal.
"""

# Exactly one per point.
FUNCTIONS = {"sensor", "sp", "cmd"}

# Marker tags, grouped only to keep the list readable in prompts.
VOCABULARY: dict[str, set[str]] = {
    "function": FUNCTIONS,
    "substance": {"air", "water", "hot-water", "chilled-water", "condenser-water", "refrig", "naturalGas", "elec"},
    "quantity": {"temp", "humidity", "flow", "pressure", "delta", "speed", "power", "energy", "co2", "wetBulb"},
    "position": {
        "discharge", "return", "outside", "mixed", "exhaust", "zone", "entering", "leaving",
        "hotDeck", "coldDeck", "primaryLoop", "secondaryLoop", "suction",
    },
    "component": {
        "damper", "valve", "fan", "pump", "coil", "compressor", "condenser", "evaporator",
        "filter", "bypass", "reheat", "economizer", "stage",
    },
    "process": {"heating", "cooling"},
    "state": {"run", "enable", "occupied", "hvacMode", "alarm", "fault"},
}
TAGS: set[str] = set().union(*VOCABULARY.values())

EQUIPMENT_TYPES = {
    "ahu", "ahu-dualDuct", "rtu", "fcu", "vav", "vav-parallel", "vav-series", "vav-dualDuct",
    "boiler", "chiller", "coolingTower", "pump", "hot-water-plant", "chilled-water-plant",
    "condenser-water-plant", "other",
}


def validate_tags(tags: list[str]) -> list[str]:
    """Return problems with a point's tag set (empty list means valid)."""
    problems = []
    if not tags:
        return ["no tags"]
    unknown = sorted(set(tags) - TAGS)
    if unknown:
        problems.append(f"unknown tags {unknown}")
    if len(tags) != len(set(tags)):
        problems.append("duplicate tags")
    functions = FUNCTIONS & set(tags)
    if len(functions) != 1:
        problems.append(f"needs exactly one of sensor/sp/cmd, has {sorted(functions) or 'none'}")
    return problems


def vocabulary_text() -> str:
    """The vocabulary as prompt text, in a stable order (keeps the prompt cacheable)."""
    return "\n".join(f"- {group}: {', '.join(sorted(tags))}" for group, tags in VOCABULARY.items())
