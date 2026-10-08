"""Tiny synthesizer for hearing keys: scales, home chords, progressions and
two keys back to back or blended. numpy only; returns mono float32 audio."""
from __future__ import annotations

import io
import wave
from dataclasses import dataclass

import numpy as np

from .camelot import MAJOR, Key, pitch_class
from .theory import scale

SR = 44100
BEAT = 0.5  # seconds per beat (120 BPM)

# Chord degrees (0-based scale degree of the root) for a progression that sounds
# like the key in dance music: major I–V–vi–IV, minor i–VI–III–VII.
PROGRESSION = {MAJOR: (0, 4, 5, 3), "A": (0, 5, 2, 6)}


@dataclass(frozen=True)
class Note:
    start: float  # seconds
    length: float
    midi: int
    gain: float = 0.25


def freq(midi: int) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def tonic_midi(key: Key, low: int = 55) -> int:
    """Tonic placed in the octave starting at ``low`` (G3 by default)."""
    pc = pitch_class(key)
    return low + (pc - low) % 12


def scale_midi(key: Key) -> list[int]:
    """Eight ascending notes, tonic to tonic."""
    root = tonic_midi(key)
    s = scale(key)
    out = [root + (pc - s[0]) % 12 for pc in s]
    return out + [root + 12]


def chord_midi(key: Key, degree: int) -> list[int]:
    """Triad on a scale degree, voiced near middle C, with a bass root."""
    s = scale(key)
    pcs = [s[degree % 7], s[(degree + 2) % 7], s[(degree + 4) % 7]]
    root = 57 + (pcs[0] - 57) % 12
    notes = [root]
    for pc in pcs[1:]:
        n = root + (pc - root) % 12
        notes.append(n)
    return [root - 12] + notes


def _voice(f: float, n: int, gain: float) -> np.ndarray:
    """Soft electric-piano-ish tone: a few decaying harmonics."""
    t = np.arange(n) / SR
    out = np.zeros(n, dtype=np.float64)
    for h, amp, decay in ((1, 1.0, 1.6), (2, 0.45, 3.0), (3, 0.18, 4.5), (4, 0.08, 6.0)):
        if f * h < SR / 2:
            out += amp * np.exp(-decay * t) * np.sin(2 * np.pi * f * h * t)
    attack = min(n, int(0.008 * SR))
    release = min(n, int(0.06 * SR))
    env = np.ones(n)
    if attack:
        env[:attack] = np.linspace(0, 1, attack)
    if release:
        env[-release:] *= np.linspace(1, 0, release)
    return out * env * gain


def render(notes: list[Note], tail: float = 0.3) -> np.ndarray:
    if not notes:
        return np.zeros(0, dtype=np.float32)
    total = max(n.start + n.length for n in notes) + tail
    buf = np.zeros(int(total * SR) + 1)
    for n in notes:
        i = int(n.start * SR)
        seg = _voice(freq(n.midi), int(n.length * SR), n.gain)
        buf[i:i + len(seg)] += seg
    peak = np.max(np.abs(buf)) or 1.0
    return (buf / peak * 0.8).astype(np.float32)


def scale_notes(key: Key, start: float = 0.0) -> list[Note]:
    up = scale_midi(key)
    seq = up + up[-2::-1]
    return [Note(start + i * BEAT / 2, BEAT / 2 * 1.4, m, 0.3) for i, m in enumerate(seq)]


def chord_notes(key: Key, start: float = 0.0) -> list[Note]:
    """Home chord arpeggiated, then held."""
    c = chord_midi(key, 0)
    arp = [Note(start + i * BEAT / 2, BEAT, m, 0.28) for i, m in enumerate(c[1:])]
    return arp + [Note(start + 2 * BEAT, 3 * BEAT, m, 0.22) for m in c]


def progression_notes(key: Key, start: float = 0.0, bars: int = 4) -> list[Note]:
    degrees = PROGRESSION[key.mode]
    out = []
    for i in range(bars):
        t = start + i * 2 * BEAT
        out += [Note(t, 2 * BEAT, m, 0.2) for m in chord_midi(key, degrees[i % len(degrees)])]
    return out


def key_reference(key: Key, kind: str) -> np.ndarray:
    """kind: 'scale' | 'chord' | 'progression'."""
    fn = {"scale": scale_notes, "chord": chord_notes, "progression": progression_notes}[kind]
    return render(fn(key))


def transition(k1: Key, k2: Key) -> np.ndarray:
    """The first key's progression, then the second's: hear the move."""
    a = progression_notes(k1)
    return render(a + progression_notes(k2, start=8 * BEAT + BEAT / 2))


def blend(k1: Key, k2: Key) -> np.ndarray:
    """Both home chords at once, like a long DJ blend: smooth moves sound consonant,
    clashes sound sour."""
    a = progression_notes(k1, bars=2)
    both = [Note(n.start + 4 * BEAT, n.length, n.midi, n.gain) for n in progression_notes(k1, bars=2)]
    both += [Note(n.start + 4 * BEAT, n.length, n.midi, n.gain) for n in progression_notes(k2, bars=2)]
    b = progression_notes(k2, start=8 * BEAT, bars=2)
    return render(a + both + b)


def wav_bytes(samples: np.ndarray, sr: int = SR) -> bytes:
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()
