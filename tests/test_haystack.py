from collections import Counter

import pytest

from hvac_fdd_agents import paths
from hvac_fdd_agents.data import points_config
from hvac_fdd_agents.data.haystack import validate_tags

CONFIGS = sorted(paths.POINTS.glob("*.yaml"))


def test_valid_tag_set():
    assert validate_tags(["discharge", "air", "temp", "sensor"]) == []


@pytest.mark.parametrize("tags, problem", [
    ([], "no tags"),
    (["discharge", "air", "temp"], "exactly one of sensor/sp/cmd"),
    (["air", "temp", "sensor", "sp"], "exactly one of sensor/sp/cmd"),
    (["air", "tmp", "sensor"], "unknown tags"),
    (["air", "air", "sensor"], "duplicate tags"),
])
def test_invalid_tag_sets(tags, problem):
    assert any(problem in p for p in validate_tags(tags))


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_reference_mappings_are_expressible(path):
    """Every answer key must use only tags the agents are allowed to use, with no ambiguity per unit."""
    cfg = points_config.load(path)
    for name, p in cfg.points.items():
        if p.kept:
            assert validate_tags(p.tags) == [], name
            assert p.unit in cfg.units, name
    sets = Counter((p.unit, frozenset(p.tags)) for p in cfg.points.values() if p.kept)
    assert [k for k, n in sets.items() if n > 1] == []
