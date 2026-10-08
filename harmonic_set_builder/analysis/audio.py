"""Decode an audio file to mono float32 at a fixed sample rate."""
from __future__ import annotations

import shutil
import subprocess

import numpy as np

SR = 22050


def load(path: str, sr: int = SR) -> np.ndarray:
    """ffmpeg when available (handles mp3/m4a/flac/ogg alike), else librosa."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        out = subprocess.run(
            [ffmpeg, "-v", "error", "-nostdin", "-i", path, "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
            capture_output=True, check=False,
        )
        if out.returncode == 0 and out.stdout:
            return np.frombuffer(out.stdout, dtype="<f4").astype(np.float32)
        err = out.stderr.decode(errors="replace").strip().splitlines()
        raise RuntimeError(f"ffmpeg could not decode the file: {err[-1] if err else 'no output'}")
    import librosa

    y, _ = librosa.load(path, sr=sr, mono=True)
    return y.astype(np.float32)
