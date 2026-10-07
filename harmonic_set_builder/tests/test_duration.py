from harmonic_set_builder.core.duration import Item, estimate, fmt, overlap_seconds


def test_back_to_back_and_mixed():
    items = [Item(300, 128), Item(240, 128), Item(360, 128)]
    d = estimate(items, overlap_bars=16)
    assert d.back_to_back == 900
    assert d.mixed == 900 - 2 * 30  # 16 bars at 128 BPM = 30 s per transition
    assert (d.tracks, d.unknown) == (3, 0)


def test_overlap_uses_incoming_tempo_and_caps_at_half_a_track():
    assert overlap_seconds(Item(300, 128), Item(300, 174), 16) == 16 * 4 * 60 / 174
    assert overlap_seconds(Item(40, 128), Item(300, 128), 16) == 20  # half of the short one
    assert overlap_seconds(Item(300, None), Item(300, None), 16) == 0
    assert overlap_seconds(Item(300, 128), Item(300, 128), 0) == 0


def test_unknown_durations_are_counted_not_summed():
    d = estimate([Item(300, 128), Item(None, 128), Item(300, 128)], 16)
    assert d.back_to_back == 600 and d.mixed == 570 and d.unknown == 1


def test_empty():
    d = estimate([], 16)
    assert (d.back_to_back, d.mixed, d.tracks) == (0, 0, 0)


def test_fmt():
    assert fmt(0) == "0:00"
    assert fmt(605.4) == "10:05"
    assert fmt(3725) == "1:02:05"
