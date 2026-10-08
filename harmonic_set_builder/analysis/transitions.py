"""Where to mix: transition points and phrase compatibility for A → B (spec §5).

Bars are each track's own bars; with the tempos matched, a bar of A lasts as long
as a bar of B, so "start B at A's bar N" lines the two phrase grids up.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from .labels import BREAKDOWN, DROP, INTRO, OUTRO, PHRASE, Section, first, last
from .store import TrackAnalysis


@dataclass(frozen=True)
class Tip:
    kind: str  # intro_over_outro | drop_on_breakdown | no_outro
    text: str
    a_bar: int  # start B when A reaches this bar
    a_sec: float
    b_bar: int = 0  # from this bar of B (its start, usually)
    b_sec: float = 0.0
    overlap_bars: int = 0


@dataclass(frozen=True)
class Flag:
    text: str
    severity: str = "warn"  # warn | info


def _bar_word(n: int) -> str:
    return f"{n} bar" if n == 1 else f"{n} bars"


def _adj(n: int) -> str:
    return f"{n}-bar"


def _next_after(sections: Sequence[Section], s: Optional[Section]) -> Optional[Section]:
    if s is None:
        return None
    i = sections.index(s)
    return sections[i + 1] if i + 1 < len(sections) else None


def suggest(a: TrackAnalysis, b: TrackAnalysis) -> tuple[list[Tip], list[Flag]]:
    tips: list[Tip] = []
    flags: list[Flag] = []
    if not a.sections or not b.sections:
        return tips, [Flag("Analyze both tracks to get transition points.", "info")]
    a_end = a.sections[-1].end_bar
    outro = last(a.sections, OUTRO)
    intro = first(b.sections, INTRO)
    b_next = _next_after(b.sections, intro) if intro else b.sections[0]
    b_lead = intro.bars if intro else 0

    if outro and intro:
        start = outro.end_bar - intro.bars
        what = b_next.name if b_next else "next section"
        if intro.bars == outro.bars:
            text = (f"Start B's {_adj(intro.bars)} intro at A's outro (bar {outro.start_bar + 1}): "
                    f"B's {what} lands as A ends.")
        elif start >= outro.start_bar:
            text = (f"Start B's {_adj(intro.bars)} intro {_bar_word(start - outro.start_bar)} into A's "
                    f"{_adj(outro.bars)} outro (bar {start + 1}), so B's {what} lands as A's outro ends.")
        else:
            text = (f"B's intro ({_bar_word(intro.bars)}) is longer than A's outro ({_bar_word(outro.bars)}): start "
                    f"B at A's bar {start + 1}, {_bar_word(outro.start_bar - start)} before A's outro, so B's {what} "
                    "lands as A ends.")
        start = max(start, 0)
        tips.append(Tip("intro_over_outro", text, start, a.bar_time(start), 0, 0.0, min(intro.bars, a_end - start)))
    elif intro:
        start = max(a_end - intro.bars, 0)
        tips.append(Tip("no_outro", f"A has no outro: start B's {_adj(intro.bars)} intro at A's bar {start + 1}, "
                        f"{_bar_word(intro.bars)} before A ends.", start, a.bar_time(start), 0, 0.0, intro.bars))
    elif outro:
        tips.append(Tip("no_intro", f"B has no intro: bring B in on the last bar of A's outro (bar {outro.end_bar}) "
                        "with a cut or an effect.", max(outro.end_bar - 1, 0), a.bar_time(max(outro.end_bar - 1, 0)),
                        0, 0.0, 1))

    a_break = last(a.sections, BREAKDOWN)
    b_drop = first(b.sections, DROP)
    if a_break and b_drop and a_break.end_bar < a_end:
        start = a_break.end_bar - b_drop.start_bar
        if start >= 0:
            tips.append(Tip(
                "drop_on_breakdown",
                f"Drop B's {b_drop.name} on A's last breakdown: start B at A's bar {start + 1} so B's drop "
                f"(B bar {b_drop.start_bar + 1}) hits at A's bar {a_break.end_bar + 1}, where A's own drop would come. "
                "Swap the basses there.",
                start, a.bar_time(start), 0, 0.0, b_drop.start_bar,
            ))

    if not intro:
        flags.append(Flag(f"B has no intro: it starts straight into its {b.sections[0].name}."))
    if not outro:
        flags.append(Flag(f"A has no outro: it ends on its {a.sections[-1].name}."))
    if intro and outro and intro.bars < outro.bars:
        flags.append(Flag(f"B's intro ({_bar_word(intro.bars)}) is shorter than A's outro ({_bar_word(outro.bars)}): "
                          "start B later in the outro, or the mix runs out of intro before A finishes."))
    off = []
    if outro and outro.start_bar % PHRASE:
        off.append(f"A's outro starts at bar {outro.start_bar + 1}, off the {PHRASE}-bar phrase grid")
    if intro and b_lead % PHRASE:
        off.append(f"B's intro is {_bar_word(b_lead)}, not a multiple of {PHRASE}")
    if off:
        flags.append(Flag("Phrase grids won't line up: " + "; ".join(off) + "."))
    if a.bpm and b.bpm and abs(a.bpm - b.bpm) / a.bpm > 0.08:
        flags.append(Flag(f"Tempos differ by {abs(a.bpm - b.bpm):.1f} BPM; bar counts assume they're matched.", "info"))
    return tips, flags


@dataclass(frozen=True)
class FlowTrack:
    index: int  # position in the set
    offset: float  # seconds into the set where this track starts
    times: list[float]  # bar times within the set
    energy: list[float]
    mix_in: Optional[float]  # when the next track comes in (set time)


def set_flow(analyses: Sequence[Optional[TrackAnalysis]], default_overlap_bars: int = 16) -> list[FlowTrack]:
    """Lay each analyzed track's energy curve out along the set, overlapping at the
    first suggested transition point (or ``default_overlap_bars`` before it ends)."""
    out: list[FlowTrack] = []
    offset = 0.0
    for i, a in enumerate(analyses):
        if a is None or not a.bars:
            continue
        nxt = analyses[i + 1] if i + 1 < len(analyses) else None
        mix_in = None
        if nxt is not None and nxt.sections and a.sections:
            tips, _ = suggest(a, nxt)
            if tips:
                mix_in = tips[0].a_sec
        if mix_in is None and i + 1 < len(analyses):
            bars = a.bar_times
            k = max(len(bars) - default_overlap_bars, 0)
            mix_in = bars[k] if bars else a.duration
        out.append(FlowTrack(i, offset, [offset + t for t in a.bar_times], [b["energy"] for b in a.bars],
                             offset + mix_in if mix_in is not None else None))
        offset += mix_in if mix_in is not None else a.duration
    return out
