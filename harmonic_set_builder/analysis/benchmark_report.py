"""Benchmark results as a self-contained HTML page: per track, your hot cues on top
and each method's cue positions underneath, coloured by how close they come."""
from __future__ import annotations

import html

W = 940  # timeline width in px
ROW = 22
LEFT = 250

CSS = """
:root { --bg:#ffffff; --fg:#1d1d1f; --dim:#6b6b70; --rule:#e2e2e6; --you:#d9771a; --hit:#2f9e44; --near:#d4a017;
        --miss:#a7a7ad; --card:#f6f6f8; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#16161a; --fg:#e8e8ea; --dim:#9a9aa2;
        --rule:#2c2c33; --you:#f0902a; --hit:#4cc766; --near:#e8c040; --miss:#5c5c66; --card:#1e1e24; } }
body { background:var(--bg); color:var(--fg); font:14px/1.45 system-ui, sans-serif; margin:0; padding:24px 16px; }
main { max-width:1220px; margin:0 auto; }
h1 { font-size:22px; margin:0 0 4px; } h2 { font-size:15px; margin:0; }
p.dim, .dim { color:var(--dim); }
table { border-collapse:collapse; margin:12px 0 24px; font-variant-numeric:tabular-nums; }
th, td { padding:5px 10px; border-bottom:1px solid var(--rule); text-align:right; }
th:first-child, td:first-child { text-align:left; }
.card { background:var(--card); border-radius:8px; padding:12px 14px; margin:14px 0; overflow-x:auto; }
.legend span { display:inline-block; margin-right:16px; }
.sw { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:5px; vertical-align:-1px; }
svg text { fill:var(--fg); font-size:12px; } svg .lab { fill:var(--dim); font-size:10px; }
"""


def _fmt(sec: float) -> str:
    return f"{int(sec // 60)}:{sec % 60:04.1f}"


def _track_svg(t: dict, method_names: dict[str, str], bar: float) -> str:
    cues = [c for c in t["hot_cues"]]
    methods = [(k, v) for k, v in t["methods"].items() if k in method_names]
    ends = [c for c in cues] + [p[0] for _, v in methods for p in v]
    dur = max(ends + [60.0]) * 1.03
    x = lambda s: LEFT + (W - LEFT - 10) * s / dur  # noqa: E731
    rows = [("You (hot cues)", [(c, "", "you") for c in cues])]
    for k, preds in methods:
        marks = []
        for p in preds:
            s, lab = p[0], p[1] if len(p) > 1 else ""
            d = min((abs(s - c) for c in cues), default=1e9)
            kind = "hit" if d <= 0.5 else "near" if d <= bar else "miss"
            marks.append((s, lab if lab not in ("cue",) else "", kind))
        rows.append((method_names[k], marks))
    h = ROW * len(rows) + 26
    out = [f'<svg width="{W}" height="{h}" viewBox="0 0 {W} {h}" role="img">']
    for m in range(0, int(dur) + 1, 30):  # time grid every 30 s
        out.append(f'<line x1="{x(m):.1f}" x2="{x(m):.1f}" y1="0" y2="{h - 18}" stroke="var(--rule)"/>')
        out.append(f'<text class="lab" x="{x(m):.1f}" y="{h - 4}" text-anchor="middle">{_fmt(m)[:-2]}</text>')
    for c in cues:  # faint guide down from each of your cues
        out.append(f'<line x1="{x(c):.1f}" x2="{x(c):.1f}" y1="{ROW}" y2="{h - 18}" stroke="var(--you)" '
                   f'stroke-opacity=".25" stroke-dasharray="2 3"/>')
    for i, (name, marks) in enumerate(rows):
        y = i * ROW + 4
        out.append(f'<text x="0" y="{y + 13}">{html.escape(name)}</text>')
        for s, lab, kind in marks:
            colour = f"var(--{kind})"
            tip = html.escape(f"{name}: {_fmt(s)}" + (f" {lab}" if lab else ""))
            out.append(f'<rect x="{x(s) - 2:.1f}" y="{y + 2}" width="4" height="{ROW - 8}" rx="1" fill="{colour}">'
                       f'<title>{tip}</title></rect>')
            if lab and not lab.startswith("r"):
                out.append(f'<text class="lab" x="{x(s) + 4:.1f}" y="{y + 12}">{html.escape(lab[:9])}</text>')
    out.append("</svg>")
    return "".join(out)


def render(data: dict, method_names: dict[str, str]) -> str:
    scores = data.get("scores", [])
    head = ["Method", "Your cues found (±0.5 s)", "(±1 bar)", "Predictions near a cue", "Predictions/track"]
    rows = "".join(
        f"<tr><td>{html.escape(s['method'])}</td><td>{100 * s['recall_half_sec']:.0f}%</td>"
        f"<td>{100 * s['recall_bar']:.0f}%</td><td>{100 * s['precision_half_sec']:.0f}%</td>"
        f"<td>{s['preds'] / max(s['tracks'], 1):.1f}</td></tr>" for s in scores)
    cards = []
    for t in data.get("tracks", []):
        bar = 240.0 / t["bpm"] if t.get("bpm") else 2.0
        meta = " · ".join(x for x in (t.get("genre") or "", f"{t['bpm']:.0f} BPM" if t.get("bpm") else "") if x)
        cards.append(f'<div class="card"><h2>{html.escape(t["track"])}</h2><div class="dim">{html.escape(meta)} · '
                     f'{len(t["hot_cues"])} hot cues</div>{_track_svg(t, method_names, bar)}</div>')
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
            f"content='width=device-width, initial-scale=1'><title>Analyzer Benchmark</title><style>{CSS}</style>"
            f"</head><body><main><h1>Analyzer benchmark</h1><p class='dim'>Each method's cue positions against the hot "
            f"cues you placed in Mixxx. Hover a mark for its time and label.</p>"
            f"<table><tr>{''.join(f'<th>{h}</th>' for h in head)}</tr>{rows}</table>"
            f"<div class='legend'><span><i class='sw' style='background:var(--you)'></i>your hot cue</span>"
            f"<span><i class='sw' style='background:var(--hit)'></i>within 0.5 s</span>"
            f"<span><i class='sw' style='background:var(--near)'></i>within a bar</span>"
            f"<span><i class='sw' style='background:var(--miss)'></i>no cue of yours nearby</span></div>"
            f"{''.join(cards)}</main></body></html>")


def write(path, data: dict, method_names: dict[str, str]) -> None:
    from pathlib import Path

    Path(path).write_text(render(data, method_names), encoding="utf-8")

