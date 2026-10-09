"""Runs inside the separate CUE-DETR Python environment. Standalone: imports
nothing from the app.

CUE-DETR (Argüello, Lanzendörfer & Wattenhofer, ISMIR 2024; MIT licence) is a
DETR object detector fine-tuned on 21k cue points that DJs placed in 4,710 EDM
tracks. It predicts *where* cues go, not what the section is. This follows the
authors' cue_points.py (github.com/ETH-DISCO/cue-detr) for one file, and also
returns every candidate's score so the app can apply its own threshold.

Usage: python cuedetr_worker.py AUDIO OUT_JSON [SENSITIVITY] [RADIUS]
Writes {"analyzer", "cues": [seconds], "candidates": [[seconds, score], ...]}.
"""
import json
import os
import sys
import warnings

OVERLAP = 0.75
W_WIN = 355
PADDING = 266
CHECKPOINT = "disco-eth/cue-detr"


def main() -> int:
    audio, out_json = sys.argv[1:3]
    sensitivity = float(sys.argv[3]) if len(sys.argv) > 3 else 0.9
    radius = int(sys.argv[4]) if len(sys.argv) > 4 else 16
    warnings.filterwarnings("ignore")
    try:  # nice 10, absolute
        if os.getpriority(os.PRIO_PROCESS, 0) < 10:
            os.setpriority(os.PRIO_PROCESS, 0, 10)
    except (AttributeError, OSError):
        pass
    import librosa
    import numpy as np
    import torch
    from matplotlib import cm
    from PIL import Image
    from scipy.signal import find_peaks
    from transformers import DetrForObjectDetection, DetrImageProcessor

    processor = DetrImageProcessor.from_pretrained("facebook/detr-resnet-50")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = DetrForObjectDetection.from_pretrained(CHECKPOINT).to(device)

    y, _ = librosa.load(audio, sr=22050)
    m_db = librosa.power_to_db(librosa.feature.melspectrogram(y=y, sr=22050, n_fft=2048), ref=np.max)
    arr = m_db[::-1]
    sm = cm.ScalarMappable(cmap="viridis")
    sm.set_clim(arr.min(), arr.max())
    rgba = np.require(sm.to_rgba(arr, bytes=True), requirements="C")
    image = np.array(Image.frombuffer("RGBA", (rgba.shape[1], rgba.shape[0]), rgba, "raw", "RGBA", 0, 1))[:, :, :3]

    n_windows = int(np.floor((image.shape[1] + PADDING) / (W_WIN * (1 - OVERLAP))))
    images, borders = [], []
    for i in range(n_windows):
        left = int(np.floor(i * W_WIN * (1 - OVERLAP))) - PADDING
        right = left + W_WIN
        borders.append(left)
        if left < 0:
            seg = np.pad(image[:, :right], ((0, 0), (-left, 0), (0, 0)), mode="linear_ramp")
        elif right > image.shape[1]:
            seg = image[:, left:]
            seg = np.pad(seg, ((0, 0), (0, right - left - seg.shape[1]), (0, 0)), mode="linear_ramp")
        else:
            seg = image[:, left:right]
        images.append(seg)

    pixel_values = processor.preprocess(images, do_resize=False, return_tensors="pt")["pixel_values"].to(device)
    with torch.no_grad():
        outputs = model(pixel_values)
    predictions = processor.post_process_object_detection(outputs, 0, [(128, 355)] * pixel_values.shape[0])
    scores, positions = [], []
    for p, left in zip(predictions, borders):
        scores.extend(p["scores"].tolist())
        positions.extend(((p["boxes"][:, 0] + p["boxes"][:, 2]) // 2 + left).long().tolist())
    positions, scores = zip(*sorted(zip(positions, scores)))
    s = np.asarray(scores)
    scaled = (s - s.min()) / (s.max() - s.min() or 1)
    peaks, _ = find_peaks(scaled, height=sensitivity, distance=radius)
    to_sec = lambda frames: [float(t) for t in librosa.frames_to_time(np.asarray(frames), sr=22050)]  # noqa: E731
    data = {
        "analyzer": "cue-detr",
        "cues": to_sec([positions[i] for i in peaks]),
        "candidates": [[t, round(float(v), 4)] for t, v in zip(to_sec(positions), scaled)],
    }
    tmp = out_json + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, out_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
