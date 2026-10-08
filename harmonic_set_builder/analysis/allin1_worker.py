"""Runs inside the separate allin1 Python environment (allin1 can't run on the
app's Python). Standalone: imports nothing from the app.

Usage: python allin1_worker.py AUDIO OUT_JSON WORK_DIR
Writes {"analyzer", "bpm", "beats", "downbeats", "segments": [{"start", "end", "label"}]}.
"""
import json
import os
import sys
import warnings


def main() -> int:
    audio, out_json, work = sys.argv[1:4]
    warnings.filterwarnings("ignore")
    try:  # nice 10, absolute: stay out of the way of anything audio-critical
        if os.getpriority(os.PRIO_PROCESS, 0) < 10:
            os.setpriority(os.PRIO_PROCESS, 0, 10)
    except (AttributeError, OSError):
        pass
    import allin1

    try:
        from importlib.metadata import version
        ver = version("allin1")
    except Exception:  # noqa: BLE001
        ver = "?"
    r = allin1.analyze(
        audio,
        out_dir=None,
        demix_dir=os.path.join(work, "demix"),
        spec_dir=os.path.join(work, "spec"),
        device="cpu",
        keep_byproducts=False,
        multiprocess=False,
    )
    data = {
        "analyzer": f"allin1=={ver}",
        "bpm": float(r.bpm) if r.bpm else None,
        "beats": [float(b) for b in r.beats],
        "downbeats": [float(b) for b in r.downbeats],
        "segments": [{"start": float(s.start), "end": float(s.end), "label": str(s.label)} for s in r.segments],
    }
    tmp = out_json + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, out_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
