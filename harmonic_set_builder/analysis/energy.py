"""Per-bar features, the combined energy curve, and build-up detection (spec §3.3–3.4)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

HOP = 512
N_FFT = 2048
LOW_BAND = (20.0, 150.0)  # kick / bass
HIGH_BAND = 5000.0  # hats, risers, air
# How the components combine into one energy value (each normalized 0–1 per track).
WEIGHTS = {"rms": 0.3, "low": 0.3, "high": 0.15, "centroid": 0.1, "onsets": 0.15}
COMPONENTS = tuple(WEIGHTS)


@dataclass
class BarFeatures:
    starts: np.ndarray  # bar start times, seconds
    ends: np.ndarray
    rms: np.ndarray  # all normalized 0–1 per track
    low: np.ndarray
    high: np.ndarray
    centroid: np.ndarray
    onsets: np.ndarray
    energy: np.ndarray
    timbre: np.ndarray  # (bars, n) MFCC + chroma means, for segmentation
    low_raw: np.ndarray  # low-band level in dB, for kick presence

    def __len__(self) -> int:
        return len(self.starts)

    def matrix(self) -> np.ndarray:
        """Bars × features, standardized, energy features weighted up (EDM sections
        are defined by what drops in and out more than by melody)."""
        energy = np.stack([self.rms, self.low, self.high, self.centroid, self.onsets], axis=1) * 2.0
        m = np.concatenate([energy, self.timbre], axis=1)
        sd = m.std(axis=0)
        sd[sd == 0] = 1
        return (m - m.mean(axis=0)) / sd


def normalize(x: np.ndarray) -> np.ndarray:
    """Robust 0–1 scaling (5th–95th percentile) so one loud bar doesn't flatten the rest."""
    x = np.asarray(x, dtype=float)
    if len(x) == 0:
        return x
    lo, hi = np.percentile(x, 5), np.percentile(x, 95)
    if hi - lo < 1e-9:
        return np.full_like(x, 0.5)
    return np.clip((x - lo) / (hi - lo), 0, 1)


def _aggregate(values: np.ndarray, frame_times: np.ndarray, starts: np.ndarray, ends: np.ndarray, fn=np.mean):
    idx0 = np.searchsorted(frame_times, starts)
    idx1 = np.maximum(np.searchsorted(frame_times, ends), idx0 + 1)
    out = []
    for a, b in zip(idx0, idx1):
        seg = values[..., a:b]
        out.append(fn(seg, axis=-1) if seg.shape[-1] else np.zeros(values.shape[:-1]))
    return np.array(out)


@dataclass
class Spectral:
    """Frame-level features shared by beat- and bar-level aggregation."""

    times: np.ndarray
    rms: np.ndarray
    low_db: np.ndarray
    high_db: np.ndarray
    centroid: np.ndarray
    onset_env: np.ndarray
    onset_frames: np.ndarray
    mfcc: np.ndarray
    chroma: np.ndarray


def spectral(y: np.ndarray, sr: int) -> Spectral:
    import librosa

    S = np.abs(librosa.stft(y, n_fft=N_FFT, hop_length=HOP)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=N_FFT)
    low = S[(freqs >= LOW_BAND[0]) & (freqs < LOW_BAND[1])].sum(axis=0)
    high = S[freqs >= HIGH_BAND].sum(axis=0)
    mel = librosa.feature.melspectrogram(S=S, sr=sr, n_mels=64)
    onset_env = librosa.onset.onset_strength(S=librosa.power_to_db(mel), sr=sr, hop_length=HOP)
    onset_frames = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, hop_length=HOP)
    return Spectral(
        times=librosa.frames_to_time(np.arange(S.shape[1]), sr=sr, hop_length=HOP),
        rms=librosa.feature.rms(S=np.sqrt(S), frame_length=N_FFT, hop_length=HOP)[0],
        low_db=10 * np.log10(low + 1e-10),
        high_db=10 * np.log10(high + 1e-10),
        centroid=librosa.feature.spectral_centroid(S=np.sqrt(S), sr=sr)[0],
        onset_env=onset_env,
        onset_frames=onset_frames,
        mfcc=librosa.feature.mfcc(S=librosa.power_to_db(mel), n_mfcc=13),
        chroma=librosa.feature.chroma_stft(S=S, sr=sr, n_fft=N_FFT, hop_length=HOP),
    )


def beat_novelty(sp: Spectral, beats: np.ndarray, duration: float) -> np.ndarray:
    """How much the low band and loudness change at each beat (for the downbeat phase)."""
    ends = np.append(beats[1:], duration)
    low = _aggregate(sp.low_db, sp.times, beats, ends)
    rms = _aggregate(sp.rms, sp.times, beats, ends)
    nov = np.abs(np.diff(normalize(low), prepend=low[:1])) + np.abs(np.diff(normalize(rms), prepend=rms[:1]))
    return nov


def bar_features(sp: Spectral, bars: np.ndarray, duration: float) -> BarFeatures:
    starts = np.asarray(bars, dtype=float)
    ends = np.append(starts[1:], duration)
    rms = _aggregate(sp.rms, sp.times, starts, ends)
    low = _aggregate(sp.low_db, sp.times, starts, ends)
    high = _aggregate(sp.high_db, sp.times, starts, ends)
    cent = _aggregate(sp.centroid, sp.times, starts, ends)
    onset_t = sp.times[np.clip(sp.onset_frames, 0, len(sp.times) - 1)]
    counts = np.array([((onset_t >= a) & (onset_t < b)).sum() / max(b - a, 1e-3) for a, b in zip(starts, ends)])
    timbre = np.concatenate([
        _aggregate(sp.mfcc, sp.times, starts, ends),
        _aggregate(sp.chroma, sp.times, starts, ends),
    ], axis=1) if len(starts) else np.zeros((0, 25))
    f = {"rms": normalize(rms), "low": normalize(low), "high": normalize(high),
         "centroid": normalize(cent), "onsets": normalize(counts)}
    energy = sum(WEIGHTS[k] * f[k] for k in COMPONENTS)
    return BarFeatures(starts, ends, f["rms"], f["low"], f["high"], f["centroid"], f["onsets"],
                       normalize(energy) if len(energy) > 2 else energy, timbre, low)


def kick_present(f: BarFeatures) -> np.ndarray:
    """Per bar: is the low end (kick/bass) in? Relative to the track's own loudest low end."""
    if len(f) == 0:
        return np.zeros(0, dtype=bool)
    ref = np.percentile(f.low_raw, 90)
    return f.low_raw > ref - 9.0  # within 9 dB of the full-on low end


def _rising(x: np.ndarray) -> bool:
    """Upward trend: positive slope, or a clearly higher second half."""
    if len(x) < 2:
        return False
    half = len(x) // 2
    return _slope(x) > 0.005 or x[half:].mean() > x[:half].mean() + 0.05


def _slope(x: np.ndarray) -> float:
    if len(x) < 2:
        return 0.0
    t = np.arange(len(x))
    return float(np.polyfit(t, x, 1)[0])


def detect_build(f: BarFeatures, boundary: int, seg_start: int) -> Optional[int]:
    """Bar where a build-up starts, ending at ``boundary`` (the drop), or None.

    Looks back 16, 8, then 4 bars (never past ``seg_start``) for: rising onset
    density, high band and spectral centroid (two of the three); low end low or
    falling (kick removed); and a step up where the window starts, so calm bars
    before the riser aren't swallowed. Confirmed by a sharp jump in energy or low
    end in the first bar after the boundary.
    """
    if boundary <= 0 or boundary >= len(f):
        return None
    # A riser can be as loud as the drop, so the kick/bass arriving also confirms it.
    jump = max(f.energy[boundary] - f.energy[boundary - 1], f.low[boundary] - f.low[boundary - 1])
    if jump < 0.15:
        return None
    kick = kick_present(f)
    for w in (16, 8, 4):
        a = boundary - w
        if a < seg_start:
            continue
        win = slice(a, boundary)
        rising = sum(_rising(x[win]) for x in (f.onsets, f.high, f.centroid))
        low_ok = _slope(f.low[win]) <= 0.005 or kick[win].mean() < 0.5
        if rising >= 2 and low_ok and (a == seg_start or _step_up(f, a)):
            return a
    return None


def _step_up(f: BarFeatures, a: int) -> bool:
    """Something enters at bar ``a``: high band or onsets jump versus the two bars before."""
    if a < 2:
        return True
    for x in (f.high, f.onsets):
        if x[a:a + 2].mean() > x[a - 2:a].mean() + 0.1:
            return True
    return False
