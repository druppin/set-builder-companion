"""Camelot keys: parsing, notation conversion and move classification.

A key is a Camelot ``Key(number 1-12, mode 'A'|'B')``; A = minor, B = major.
Each wheel step is a perfect fifth, so +7 steps = one semitone up.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NamedTuple, Optional

MINOR, MAJOR = "A", "B"

# Pitch-class names used for traditional notation (index = semitones above C).
_SHARP_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
_MINOR_NAMES = ["Cm", "C#m", "Dm", "Ebm", "Em", "Fm", "F#m", "Gm", "G#m", "Am", "Bbm", "Bm"]
_LETTER_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

NOTATIONS = ("camelot", "lancelot", "openkey", "traditional")


class Key(NamedTuple):
    number: int  # 1..12
    mode: str  # 'A' (minor) | 'B' (major)

    def __str__(self) -> str:
        return f"{self.number}{self.mode}"


def _wrap(n: int) -> int:
    return (n - 1) % 12 + 1


def key_from_pitch_class(pc: int, minor: bool) -> Key:
    """Pitch class (C=0) + mode -> Camelot key. C major = 8B, A minor = 8A."""
    if minor:
        pc = (pc + 3) % 12  # relative major shares the wheel number
    return Key(_wrap(pc * 7 % 12 + 8), MINOR if minor else MAJOR)


def pitch_class(key: Key) -> int:
    pc = (key.number - 8) * 7 % 12  # inverse of *7 mod 12 is *7
    return (pc - 3) % 12 if key.mode == MINOR else pc


def from_key_id(key_id: Optional[int]) -> Optional[Key]:
    """Mixxx ``ChromaticKey`` enum (keys.proto): 0 = invalid,
    1..12 = C_MAJOR..B_MAJOR, 13..24 = C_MINOR..B_MINOR (chromatic from C)."""
    if key_id is None or not 1 <= int(key_id) <= 24:
        return None
    key_id = int(key_id)
    if key_id <= 12:
        return key_from_pitch_class(key_id - 1, minor=False)
    return key_from_pitch_class(key_id - 13, minor=True)


_RE_CAMELOT = re.compile(r"^\s*(\d{1,2})\s*([ABab])\s*$")
_RE_OPENKEY = re.compile(r"^\s*(\d{1,2})\s*([dmDM])\s*$")
_RE_TRAD = re.compile(
    r"^\s*([A-Ga-g])\s*([#♯b♭]?)\s*(m|min|minor|maj|major|M)?\s*$"
)


def parse_key(text: Optional[str]) -> Optional[Key]:
    """Parse Camelot/Lancelot ("8A"), OpenKey ("1m") or traditional ("Am", "F#",
    "Bbmin") text. Combined forms such as "8A (Am)" or "1m Am" use the first token."""
    if not text:
        return None
    text = text.strip()
    for part in [text] + re.split(r"[\s/()|,]+", text):
        if not part:
            continue
        m = _RE_CAMELOT.match(part)
        if m and 1 <= int(m.group(1)) <= 12:
            return Key(int(m.group(1)), m.group(2).upper())
        m = _RE_OPENKEY.match(part)
        if m and 1 <= int(m.group(1)) <= 12:
            return Key(_wrap(int(m.group(1)) + 7), MAJOR if m.group(2).lower() == "d" else MINOR)
        m = _RE_TRAD.match(part)
        if m:
            pc = _LETTER_PC[m.group(1).upper()]
            if m.group(2) in ("#", "♯"):
                pc += 1
            elif m.group(2) in ("b", "♭"):
                pc -= 1
            suffix = m.group(3) or ""
            minor = suffix in ("m", "min", "minor")
            return key_from_pitch_class(pc % 12, minor)
    return None


def format_key(key: Optional[Key], notation: str = "camelot") -> str:
    if key is None:
        return "?"
    if notation == "openkey":
        return f"{_wrap(key.number - 7)}{'m' if key.mode == MINOR else 'd'}"
    if notation == "traditional":
        pc = pitch_class(key)
        return _MINOR_NAMES[pc] if key.mode == MINOR else _SHARP_NAMES[pc]
    return str(key)  # camelot and lancelot share the same 1A..12B labels


def transpose(key: Key, semitones: int) -> Key:
    """Shift a key by semitones; each semitone is +7 wheel steps."""
    return Key(_wrap(key.number + 7 * semitones), key.mode)


# --------------------------------------------------------------------------
# Moves

SMOOTH, ENERGY, CLASH = "smooth", "energy", "clash"
TIER_ORDER = {SMOOTH: 0, ENERGY: 1, CLASH: 2}
TIER_NAMES = {SMOOTH: "Smooth", ENERGY: "Energy move", CLASH: "Clash"}


@dataclass(frozen=True)
class MoveDef:
    name: str
    label: str
    tier: str
    energy: Optional[int]  # None for clashes: the energy line breaks


# Section 4 move table. Labels and deltas are overridable from settings.
# Energy deltas calibrated to camelotwheel.org's Interactive Set Builder
# (app.js getHarmonicMatches/getEnergyScore, read 2026-10-07): mode flips and
# diagonals score 0, ±2 scores ±3, +7 wheel steps (semitone up) scores +2.
# Semitone down is not offered there; it mirrors semitone up.
DEFAULT_MOVES: dict[str, MoveDef] = {
    m.name: m
    for m in [
        MoveDef("Same key", "Sustain", SMOOTH, 0),
        MoveDef("+1", "Lift", SMOOTH, 1),
        MoveDef("−1", "Settle (calmer)", SMOOTH, -1),
        MoveDef("Relative major", "Brighten", SMOOTH, 0),
        MoveDef("Relative minor", "Darken (more serious)", SMOOTH, 0),
        MoveDef("Diagonal up", "Lift + brighten", SMOOTH, 0),
        MoveDef("Diagonal down", "Settle + darken", SMOOTH, 0),
        MoveDef("Diagonal down (to major)", "Settle + brighten", SMOOTH, 0),
        MoveDef("Diagonal up (to minor)", "Lift + darken", SMOOTH, 0),
        MoveDef("+2", "Energy boost (whole step up)", ENERGY, 3),
        MoveDef("−2", "Energy drop (whole step down)", ENERGY, -3),
        MoveDef("Semitone up", "Big lift (key change)", ENERGY, 2),
        MoveDef("Semitone down", "Big drop", ENERGY, -2),
        MoveDef("Tritone", "Tritone clash", CLASH, None),
        MoveDef("Off-key", "Off-key clash", CLASH, None),
    ]
}


@dataclass(frozen=True)
class Move:
    name: str
    label: str
    tier: str  # effective tier (Energy moves may be demoted to clash)
    energy: Optional[int]
    base_tier: str

    @property
    def is_clash(self) -> bool:
        return self.tier == CLASH


def wheel_distance(n1: int, n2: int) -> int:
    """(n2 - n1) mod 12 mapped into -5..+6."""
    d = (n2 - n1) % 12
    return d - 12 if d > 6 else d


def move_name(k1: Key, k2: Key) -> str:
    d = wheel_distance(k1.number, k2.number)
    if k1.mode == k2.mode:
        return {
            0: "Same key", 1: "+1", -1: "−1", 2: "+2", -2: "−2",
            -5: "Semitone up", 5: "Semitone down", 6: "Tritone",
        }.get(d, "Off-key")
    if k1.mode == MINOR:  # A -> B
        return {0: "Relative major", 1: "Diagonal up", -1: "Diagonal down (to major)", 6: "Tritone"}.get(d, "Off-key")
    return {0: "Relative minor", -1: "Diagonal down", 1: "Diagonal up (to minor)", 6: "Tritone"}.get(d, "Off-key")


def classify(
    k1: Optional[Key],
    k2: Optional[Key],
    moves: Optional[dict[str, MoveDef]] = None,
    energy_moves_in_key: bool = True,
) -> Optional[Move]:
    """Classify the transition k1 -> k2. Returns None when either key is unknown."""
    if k1 is None or k2 is None:
        return None
    d = (moves or DEFAULT_MOVES)[move_name(k1, k2)]
    tier = d.tier
    if tier == ENERGY and not energy_moves_in_key:
        tier = CLASH
    return Move(d.name, d.label, tier, d.energy, d.tier)


ALL_KEYS: list[Key] = [Key(n, m) for m in (MINOR, MAJOR) for n in range(1, 13)]
