from collections import Counter

import pytest

from harmonic_set_builder.core.camelot import (
    ALL_KEYS, CLASH, ENERGY, SMOOTH, Key, classify, format_key, from_key_id, move_name,
    parse_key, transpose, wheel_distance,
)

# Mixxx ChromaticKey enum: 1..12 = C..B major, 13..24 = C..B minor.
KEY_ID_EXPECTED = {
    0: None, 1: "8B", 2: "3B", 3: "10B", 4: "5B", 5: "12B", 6: "7B", 7: "2B", 8: "9B",
    9: "4B", 10: "11B", 11: "6B", 12: "1B", 13: "5A", 14: "12A", 15: "7A", 16: "2A",
    17: "9A", 18: "4A", 19: "11A", 20: "6A", 21: "1A", 22: "8A", 23: "3A", 24: "10A", 25: None,
}


@pytest.mark.parametrize("kid,expected", KEY_ID_EXPECTED.items())
def test_key_id_mapping(kid, expected):
    k = from_key_id(kid)
    assert (str(k) if k else None) == expected


def test_key_id_null():
    assert from_key_id(None) is None


def test_key_ids_cover_all_24_keys():
    assert {from_key_id(i) for i in range(1, 25)} == set(ALL_KEYS)


@pytest.mark.parametrize("notation", ["camelot", "lancelot", "openkey", "traditional"])
def test_notation_round_trip(notation):
    for k in ALL_KEYS:
        assert parse_key(format_key(k, notation)) == k


@pytest.mark.parametrize(
    "text,expected",
    [
        ("8A", "8A"), ("12b", "12B"), ("1m", "8A"), ("1d", "8B"), ("Am", "8A"), ("C", "8B"),
        ("F#m", "11A"), ("Gbm", "11A"), ("Bbmin", "3A"), ("C#", "3B"), ("Db", "3B"),
        ("8A (Am)", "8A"), ("1m Am", "8A"), ("", None), (None, None), ("xyz", None), ("13A", None),
    ],
)
def test_parse_key(text, expected):
    k = parse_key(text)
    assert (str(k) if k else None) == expected


def test_wheel_distance_range():
    for a in range(1, 13):
        for b in range(1, 13):
            assert -5 <= wheel_distance(a, b) <= 6


def test_every_pair_classified():
    """All 576 pairs get exactly one move; each key has the expected neighbours."""
    for k1 in ALL_KEYS:
        names = Counter(move_name(k1, k2) for k2 in ALL_KEYS)
        for n in ["Same key", "+1", "−1", "+2", "−2", "Semitone up", "Semitone down"]:
            assert names[n] == 1, (k1, n)
        assert names["Relative major" if k1.mode == "A" else "Relative minor"] == 1
        assert names["Diagonal up" if k1.mode == "A" else "Diagonal down"] == 1
        assert names["Diagonal down (to major)" if k1.mode == "A" else "Diagonal up (to minor)"] == 1
        assert names["Tritone"] == 2  # same mode and cross mode at d = 6
        assert sum(names.values()) == 24


@pytest.mark.parametrize(
    "a,b,name,tier,energy",
    [
        ("8A", "8A", "Same key", SMOOTH, 0),
        ("8A", "9A", "+1", SMOOTH, 1),
        ("8A", "7A", "−1", SMOOTH, -1),
        ("8A", "8B", "Relative major", SMOOTH, 0),
        ("8B", "8A", "Relative minor", SMOOTH, 0),
        ("8A", "9B", "Diagonal up", SMOOTH, 0),
        ("8B", "7A", "Diagonal down", SMOOTH, 0),
        ("8A", "10A", "+2", ENERGY, 3),
        ("8A", "6A", "−2", ENERGY, -3),
        ("8A", "3A", "Semitone up", ENERGY, 2),  # Am -> Bbm
        ("8A", "1A", "Semitone down", ENERGY, -2),  # Am -> G#m
        ("8A", "2A", "Tritone", CLASH, None),
        ("12A", "1A", "+1", SMOOTH, 1),
        ("1B", "12A", "Diagonal down", SMOOTH, 0),
        ("8A", "11A", "Off-key", CLASH, None),
        ("8A", "7B", "Diagonal down (to major)", SMOOTH, 0),
        ("8B", "9A", "Diagonal up (to minor)", SMOOTH, 0),
        ("1A", "12B", "Diagonal down (to major)", SMOOTH, 0),
        ("8A", "6B", "Off-key", CLASH, None),
    ],
)
def test_classify(a, b, name, tier, energy):
    m = classify(parse_key(a), parse_key(b))
    assert (m.name, m.tier, m.energy) == (name, tier, energy)


def test_semitone_moves_are_real_semitones():
    am = parse_key("Am")
    assert format_key(transpose(am, 1), "traditional") == "Bbm"
    assert move_name(am, transpose(am, 1)) == "Semitone up"
    assert move_name(am, transpose(am, -1)) == "Semitone down"
    assert move_name(am, transpose(am, 2)) == "+2"


def test_energy_moves_can_be_demoted():
    m = classify(Key(8, "A"), Key(10, "A"), energy_moves_in_key=False)
    assert m.tier == CLASH and m.base_tier == ENERGY


def test_unknown_key_is_not_a_clash():
    assert classify(None, Key(8, "A")) is None
