"""Runs inside the separate allin1 Python environment (allin1 can't run on the
app's Python). Standalone: imports nothing from the app.

Usage: python allin1_worker.py AUDIO OUT_JSON WORK_DIR [MODELS]

MODELS is a comma-separated list (default "harmonix-all"). The track is
source-separated and turned into a spectrogram once; every model then runs on
those. Besides allin1's listed models, "raveform-fold3" is an EDM-trained
checkpoint published (but not listed) in allin1's model repository. Its 11 section
labels aren't named there, so they come back as "r0".."r10" with each segment's
mean label probabilities.

One model: writes {"analyzer", "bpm", "beats", "downbeats", "segments", "timing"}.
Several: {"<model>": {...same...}, "timing": {...}}.
"""
import json
import os
import sys
import time
import warnings

EXTRA_MODELS = {"raveform-fold3": "raveform-fold3-mrkbf2f8.pth"}
FPS = 100  # allin1 activation frames per second


def _lower_priority() -> None:
    try:  # nice 10, absolute: stay out of the way of anything audio-critical
        if os.getpriority(os.PRIO_PROCESS, 0) < 10:
            os.setpriority(os.PRIO_PROCESS, 0, 10)
    except (AttributeError, OSError):
        pass


def main() -> int:
    audio, out_json, work = sys.argv[1:4]
    models = (sys.argv[4] if len(sys.argv) > 4 else "harmonix-all").split(",")
    warnings.filterwarnings("ignore")
    _lower_priority()
    from pathlib import Path

    import numpy as np
    import torch

    torch.set_num_threads(os.cpu_count() or 1)
    import allin1
    from allin1.demix import demix
    from allin1.helpers import run_inference
    from allin1.models import loaders
    from allin1.postprocessing import functional
    from allin1.spectrogram import extract_spectrograms

    try:
        from importlib.metadata import version
        ver = version("allin1")
    except Exception:  # noqa: BLE001
        ver = "?"
    loaders.NAME_TO_FILE.update(EXTRA_MODELS)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    timing = {"device": device, "threads": torch.get_num_threads()}
    path, work = Path(audio), Path(work)

    t = time.time()
    demix_paths = demix([path], work / "demix", device)
    timing["demix"] = round(time.time() - t, 1)
    t = time.time()
    spec_paths = extract_spectrograms(demix_paths, work / "spec", multiprocess=False)
    timing["spectrogram"] = round(time.time() - t, 1)

    out = {}
    for name in models:
        t = time.time()
        model = loaders.load_pretrained_model(model_name=name, device=device)
        cfg = getattr(model, "cfg", None) or getattr(getattr(model, "models", [None])[0], "cfg", None)
        n_labels = cfg.data.num_labels if cfg is not None else 10
        unnamed = n_labels != len(allin1.HARMONIX_LABELS)
        functional.HARMONIX_LABELS = [f"r{i}" for i in range(n_labels)] if unnamed else allin1.HARMONIX_LABELS
        try:
            with torch.no_grad():
                r = run_inference(path=path, spec_path=spec_paths[0], model=model, device=device,
                                  include_activations=unnamed, include_embeddings=False)
        finally:
            functional.HARMONIX_LABELS = allin1.HARMONIX_LABELS
        segs = [{"start": float(s.start), "end": float(s.end), "label": str(s.label)} for s in r.segments]
        if unnamed and r.activations is not None:
            act = np.asarray(r.activations["label"])  # (labels, frames)
            for s in segs:
                a, b = int(s["start"] * FPS), max(int(s["end"] * FPS), int(s["start"] * FPS) + 1)
                s["probs"] = act[:, a:b].mean(axis=1).round(3).tolist()
        out[name] = {
            "analyzer": f"allin1=={ver}" + ("" if name == "harmonix-all" else f":{name}"),
            "bpm": float(r.bpm) if r.bpm else None,
            "beats": [float(b) for b in r.beats],
            "downbeats": [float(b) for b in r.downbeats],
            "segments": segs,
        }
        timing[name] = round(time.time() - t, 1)
    data = dict(out[models[0]], timing=timing) if len(models) == 1 else dict(out, timing=timing)
    tmp = out_json + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, out_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
