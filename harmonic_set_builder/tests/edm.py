"""Synthetic dance track with a known structure, for analysis tests."""
from __future__ import annotations

import wave

import numpy as np

SR = 22050
BPM = 128.0
BEAT = 60.0 / BPM
BAR = 4 * BEAT

# (label, bars, parts)
LAYOUT = [
    ("Intro", 16, {"kick", "hats"}),
    ("Build", 8, {"riser", "snare_roll"}),
    ("Drop", 16, {"kick", "hats", "bass", "lead"}),
    ("Breakdown", 16, {"pad"}),
    ("Build", 8, {"riser", "snare_roll", "pad"}),
    ("Drop", 16, {"kick", "hats", "bass", "lead"}),
    ("Outro", 16, {"kick", "hats"}),
]


def _env(n, decay):
    return np.exp(-np.arange(n) / SR * decay)


def _kick():
    n = int(0.25 * SR)
    t = np.arange(n) / SR
    f = 50 + 100 * np.exp(-t * 30)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(n, 12) * 0.9


def _hat(rng):
    n = int(0.05 * SR)
    x = rng.standard_normal(n)
    x = np.diff(x, prepend=0)  # crude high-pass
    return x * _env(n, 80) * 0.15


def _snare(rng):
    n = int(0.12 * SR)
    return rng.standard_normal(n) * _env(n, 30) * 0.3


def _tone(freq, dur, amp, decay=3.0):
    n = int(dur * SR)
    t = np.arange(n) / SR
    return (np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(4 * np.pi * freq * t)) * _env(n, decay) * amp


def render(layout=LAYOUT, lead_in: float = 0.0, seed: int = 0) -> np.ndarray:
    """``lead_in`` seconds of silence before the first downbeat."""
    rng = np.random.default_rng(seed)
    total_bars = sum(b for _, b, _ in layout)
    out = np.zeros(int((lead_in + total_bars * BAR + 1.0) * SR))
    kick, snare = _kick(), _snare(rng)

    def add(sig, t):
        i = int(t * SR)
        j = min(len(out), i + len(sig))
        out[i:j] += sig[: j - i]

    bar0 = 0
    for _label, bars, parts in layout:
        for b in range(bars):
            t_bar = lead_in + (bar0 + b) * BAR
            progress = b / max(bars - 1, 1)
            for beat in range(4):
                t = t_bar + beat * BEAT
                if "kick" in parts:
                    add(kick, t)
                if "hats" in parts:
                    add(_hat(rng), t + BEAT / 2)
                if "bass" in parts:
                    add(_tone(55.0, BEAT * 0.9, 0.5, 2.0), t + BEAT / 2)
                if "snare_roll" in parts:
                    hits = 1 if progress < 0.3 else 2 if progress < 0.6 else 4
                    for h in range(hits):
                        add(snare * (0.4 + 0.6 * progress), t + h * BEAT / hits)
            if "lead" in parts:
                for k, f in enumerate((440.0, 523.3, 659.3, 523.3)):
                    add(_tone(f, BEAT, 0.12), t_bar + k * BEAT)
            if "pad" in parts:
                add(_tone(220.0, BAR, 0.15, 0.3) + _tone(261.6, BAR, 0.12, 0.3), t_bar)
            if "riser" in parts:
                n = int(BAR * SR)
                noise = np.diff(rng.standard_normal(n), prepend=0)
                add(noise * (0.02 + 0.2 * progress), t_bar)
        bar0 += bars
    return (out / np.max(np.abs(out)) * 0.9).astype(np.float32)


def write_wav(path, samples: np.ndarray, sr: int = SR) -> None:
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def expected_bounds(layout=LAYOUT) -> list[tuple[str, int, int]]:
    out, b = [], 0
    for label, bars, _ in layout:
        out.append((label, b, b + bars))
        b += bars
    return out
