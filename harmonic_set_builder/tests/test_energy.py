import json
from pathlib import Path

import pytest

from harmonic_set_builder.core.camelot import parse_key
from harmonic_set_builder.core.energy import series
from harmonic_set_builder.core.settings import Settings

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "camelotwheel_energy.json").read_text())


def keys(*names):
    return [parse_key(n) for n in names]


def values(pts):
    return [p.value for p in pts]


def test_baseline_and_deltas():
    pts = series(keys("8A", "9A", "10A", "10B", "9A"))
    assert values(pts) == [5, 6, 7, 7, 7]
    assert all(p.segment == 0 for p in pts)


def test_clash_breaks_and_carries_level():
    pts = series(keys("8A", "9A", "3B", "4B"))
    assert values(pts) == [5, 6, 6, 7]
    assert [p.segment for p in pts] == [0, 0, 1, 1]
    assert [p.clash for p in pts] == [False, False, True, False]


def test_unknown_key_carries_level_without_break():
    pts = series(keys("8A", None, "8A"))
    assert values(pts) == [5, 5, 5]
    assert not any(p.clash for p in pts)
    assert pts[1].unknown


def test_strict_mode_breaks_on_energy_moves():
    pts = series(keys("8A", "10A"), Settings(energy_moves_in_key=False))
    assert pts[1].clash and values(pts) == [5, 5]


def test_tag_mode():
    s = Settings(energy_from_tags=True)
    pts = series(keys("8A", "9A", "10A"), s, tags=[3, None, 7])
    assert values(pts) == [3, 4, 7]


def test_custom_deltas():
    s = Settings(move_overrides={"+1": {"energy": 2}})
    assert values(series(keys("8A", "9A"), s)) == [5, 7]


@pytest.mark.parametrize("fx", FIXTURES["sequences"], ids=lambda f: " → ".join(f["keys"]))
def test_camelotwheel_calibration(fx):
    """Graph shapes recorded from camelotwheel.org's Interactive Set Builder."""
    pts = series(keys(*fx["keys"]), Settings(energy_baseline=fx["values"][0]))
    assert values(pts) == fx["values"]
