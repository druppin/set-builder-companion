import pytest

from harmonic_set_builder.core.bpm import CAUTION, DANGER, SAFE, BpmSettings, compare, keylock_shift
from harmonic_set_builder.core.camelot import Key
from harmonic_set_builder.core.ranking import relate
from harmonic_set_builder.core.settings import Settings
from harmonic_set_builder.core.track import Track


@pytest.mark.parametrize(
    "out,inc,band",
    [(128, 128, SAFE), (128, 131, SAFE), (128, 131.01, CAUTION), (128, 134, CAUTION),
     (128, 134.01, DANGER), (128, 125, SAFE), (128, 122, CAUTION), (128, 121.9, DANGER)],
)
def test_band_edges(out, inc, band):
    assert compare(out, inc, BpmSettings(half_double=False)).band == band


def test_signed_delta():
    assert compare(128, 125).delta == -3
    assert compare(125, 128).delta == 3


def test_percent_mode():
    s = BpmSettings(safe=2, caution=5, percent=True, half_double=False)
    assert compare(100, 102, s).band == SAFE
    assert compare(100, 104, s).band == CAUTION
    assert compare(100, 106, s).band == DANGER


def test_half_and_double_time():
    d = compare(174, 87)
    assert d.factor == 2 and d.label == "half-time" and d.band == SAFE and d.delta == 0
    d = compare(87, 174)
    assert d.factor == 0.5 and d.label == "double-time"
    assert compare(174, 87, BpmSettings(half_double=False)).band == DANGER


def test_unknown_bpm():
    assert compare(None, 128) is None
    assert compare(128, 0) is None


def test_keylock_shift():
    assert keylock_shift(128, 128) == 0
    # 128 / 120.8 ≈ +1 semitone (2^(1/12) ≈ 1.0595)
    assert keylock_shift(128, 120.8) == 1
    assert keylock_shift(120, 128) == -1
    assert keylock_shift(174, 87, factor=2) == 0


def test_keylock_off_effective_key():
    out = Track(1, bpm=128, key=Key(8, "A"))
    inc = Track(2, bpm=120.8, key=Key(8, "A"))  # sped up a semitone -> 3A
    on = relate(out, inc, Settings())
    off = relate(out, inc, Settings(keylock=False))
    assert on.move.name == "Same key"
    assert str(off.effective_key) == "3A" and off.move.name == "Semitone up"
