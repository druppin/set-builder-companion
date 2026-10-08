# Harmonic Set Builder

A desktop companion for planning DJ sets in [Mixxx](https://mixxx.org). It suggests which track
mixes well next — by Camelot move, mood and energy change, and BPM distance — and routes the set
toward tracks you want to reach. Every suggestion shows *why* (the move, its mood, energy Δ,
BPM Δ), because the point is to build harmonic-mixing intuition.

It is a set-preparation tool, not a live controller: it reads a **read-only snapshot** of
Mixxx's library and exports finished sets as `.m3u8` for Mixxx's *Import Playlist*.

## Run

```bash
python -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/harmonic-set-builder            # or: .venv/bin/python -m harmonic_set_builder
.venv/bin/harmonic-set-builder --db /path/to/mixxxdb.sqlite   # override (saved to config)
```

Windows: `.venv\Scripts\harmonic-set-builder.exe`. The library is auto-detected in
`~/.mixxx/`, `~/.local/share/Mixxx/` (Linux), `%LOCALAPPDATA%\Mixxx\` (Windows) or Mixxx's
macOS container; *File → Choose Mixxx library…* overrides it.

Tests: `.venv/bin/pytest` (UI tests run offscreen).

## Using it

The window has three views (toolbar or *View* menu, **Ctrl+1 / 2 / 3**): **Set Builder**, **Phrases** and
**Learn**. The preview bar at the bottom is shared by all three.

### Set Builder

- **Sources** (right): click Library, a crate or a playlist to focus it. Right-click → *Open as set*
  loads a Mixxx playlist/crate as a new set (also works as an analyzer for existing sets).
- **Track table** (middle): Mixxx's columns plus Move / Mood / Energy Δ / BPM Δ / Tier / ½·2×
  against the reference track (selected setlist row, else the last track). Default order is the
  suggestion ranking; *Suggested order* returns to it. Right-click the header to choose columns.
- **Filters** (above the track table): **★ To be added only** shows just the tracks you want to play;
  **Filters ▾** adds *In key with the reference track*, *BPM from the reference track* (Safe only / Safe or
  Caution) and *Only tracks with a key and BPM*. They combine with search and "Hide tracks already in the
  set", the count shows how many tracks are visible, and the choice is remembered.
- **Setlist** (left): drag tracks in, drag rows to reorder, Delete to remove, double-click a track to
  append. Red = key clash with the previous track, amber = duplicate. Undo/redo covers everything.
- **To be added**: tracks you want to play later. Click a column header to sort it like the track table
  (right-click the header → *Unsort* to go back to the order you added them). A track leaves the list as soon as
  it's added to the set (by drag, double-click, filling a transition or a finished route) and comes back
  if it's removed from the set again, unless another copy is still in the set. Right-click (or ⋯) → *Suggest where to place it*
  or *Show me how to get here*, which opens a route of dashed transitional entries. Click the first
  one to see its candidates; drop or double-click a track to fill it.
- **Help me fix key mixing mistakes**: shows **＋** on red rows; click it to bridge the clash.
- **Energy graph** (top): energy from Camelot moves, breaking at clashes; click a point to select the row.
- **Preview** (bottom bar): click ▶ on any row, press Space in a table, or right-click → *Preview*.
  Click the same track again to pause; click the seek bar to jump, or use −10s / +10s. Tracks on an
  unmounted drive report "file not found". Uses Qt Multimedia (bundled with PySide6).
- **Cover art**: the *Art* column shows each track's cover; hover it for a larger view. The preview bar
  and the "Compared with" line show it too. Covers come from Mixxx's recorded cover file, art embedded in
  the audio (MP3/ID3, FLAC, M4A) or a `cover`/`folder`/`front` image in the album folder. They load in the
  background and are cached as small PNGs in the app data directory (`covers/`), so the drive isn't
  re-read each launch. After mounting a drive, *Refresh library* (Ctrl+R) picks up covers that were missing.
- **Set length**: under the setlist, e.g. "14 tracks · ≈ 43:11 mixed (49:41 back to back)". The mixed
  estimate subtracts a 16-bar overlap per transition at the incoming tempo (*Settings → Mixing → Mix overlap*;
  0 = back to back). With a route open it also shows the length including the transitions still to fill.
- **Sources**: clicking the Crates / Playlists headings folds them; the focused source stays highlighted.
- **Structure** column (set, track table, pool): each analyzed track's sections, e.g. `I16 B8 D32 Br16 B8 D32 O16`
  (Intro, Build, Drop, Breakdown, Groove, Outro, lengths in bars). Fills in once tracks are analyzed in Phrases.

### Phrases

Track structure: where the intro, builds, drops, breakdowns and outro are, from audio analysis.

- The left-hand tree works like the Set Builder's Sources: **Current set**, **Library**, and foldable
  **Crates** and **Playlists**, each with "tracks · analyzed ✓" counts (right-click → *Open as set* works here
  too). Click one to list its tracks; search and the status filter (*Not analyzed*, *Analyzed*, *File
  changed*, *Failed*) narrow the list, and right-clicking the header hides columns. Then
  **Analyze selected** or **Analyze all new** (everything listed that isn't analyzed yet). Results are kept and only redone when a file changes
  (mtime + size) or on *Re-analyze* (right-click). Analysis runs in background processes at low priority;
  it's meant to run while Mixxx is closed, and asks first if Mixxx is open.
- The plot shows the per-bar **energy** curve (loudness, kick/bass, highs, brightness and onset density,
  each normalized per track) and the **kick/bass** line, with the sections shaded. Click anywhere (or
  double-click a section) to play from there.
- **Transitions**: for the selected track and its neighbours in the set — e.g. "Start B's 16-bar intro at
  A's outro (bar 97)" or "Drop B's drop on A's last breakdown" — plus phrase-compatibility warnings (intro
  shorter than the outro, phrase grids that won't line up).
- **Set energy flow**: the set's tracks' energy curves laid end to end, overlapping at those mix points.
- **Analyzers**: *Built-in* (default; a few seconds per track, tuned for dance music) or *allin1*
  ([All-In-One Music Structure Analyzer](https://github.com/mir-aidj/all-in-one), a neural network; about
  **10 minutes per 3-minute track** on this laptop's CPU). allin1 can't run on the app's Python, so it runs in
  its own environment (see below). Its raw output is cached, so labeling can be re-tuned without re-running it
  (`hsb analyze --relabel`).
- Bars come from **Mixxx's own beat grid** when the track has one (decoded from `library.beats`), so section
  starts, transition points and exported cues sit on Mixxx's beats. Otherwise allin1's beats or detected beats.
- **Export cues to Mixxx…**: writes each section start as a labeled, colored hot cue (`◆ Drop 1`, …) and sets
  Mixxx's intro/outro markers from the Intro/Outro sections. A dry run is shown first; writing needs Mixxx
  closed. See *Cue export safety* below.

### Learn

- **Explore & listen**: click a key on the Camelot wheel; every key you can mix to lights up (green smooth,
  blue energy move). Click a move (or a second key) for **why it works**: which notes the two scales share and
  which change (e.g. "6 of 7 notes shared … Changes: F→F#"), how it feels, its energy, and a DJ tip.
  **Hear this key** plays its scale, home chord and a typical dance-music chord loop; **Hear the move** plays
  one key's chords then the other's, and **Hear them blended** plays both at once, so smooth moves sound
  consonant and clashes sound sour. Below, your own tracks in those keys: double-click to hear real music in
  the key.
- **Quiz** ("guess first"): name the move, in key or clash, energy up or down, find the key (answer on the
  wheel), and ear training (listen to a blend, judge it). Questions can use pairs of your own tracks (preview
  them before answering). Every answer is explained the same way as Explore. Your per-move accuracy is kept,
  and *Practise my weak moves more* asks more about the moves you miss. Number keys answer, Enter goes on.

Sets autosave (debounced 0.5 s, atomic, last 20 versions) to the app data directory
(`~/.local/share/HarmonicSetBuilder/Harmonic Set Builder/sets` on Linux). Config is one JSON file in
the app config directory.

## Phrase analysis from the command line

```bash
.venv/bin/hsb analyze                       # every library track not analyzed yet (built-in analyzer)
.venv/bin/hsb analyze --backend allin1 --limit 5
.venv/bin/hsb analyze --path ~/Music/new    # a folder instead of the Mixxx library
.venv/bin/hsb show "Chicken Soup"           # sections + an energy sparkline
.venv/bin/hsb validate --sample 20          # analyze 20 tracks across genres → CSV to check by ear
.venv/bin/hsb export-cues --dry-run         # what cue export would do; touches nothing
.venv/bin/hsb export-cues --tracks "Chicken Soup" --replace-own
```

`analyze`/`validate` refuse to run while Mixxx is open unless given `--allow-while-mixxx-runs`.

### Setting up allin1 (optional)

allin1 needs Python ≤ 3.11 (its `madmom` dependency doesn't build on 3.14) and a `natten` build matching
PyTorch. The app looks for it in `<app data>/allin1-venv` (override in *Settings → Phrase analysis*):

```bash
D="$HOME/.local/share/HarmonicSetBuilder/Harmonic Set Builder/allin1-venv"
python3.11 -m venv "$D"
"$D/bin/pip" install cython numpy==1.26.4 wheel setuptools
"$D/bin/pip" install torch==2.4.1 torchaudio==2.4.1 --index-url https://download.pytorch.org/whl/cpu
"$D/bin/pip" install --no-build-isolation git+https://github.com/CPJKU/madmom
NATTEN_CUDA_ARCH="" "$D/bin/pip" install --no-build-isolation natten==0.17.1   # CPU build, a few minutes
"$D/bin/pip" install allin1==1.1.0 demucs
```

On Windows use `py -3.11` and `Scripts\pip.exe`; the same pins apply (not yet tried there).

### Cue export safety

The only code that writes to Mixxx's database is `data/mixxx_cues.py`, and only when you confirm a write:

1. It refuses while Mixxx is running (Mixxx caches tracks and would overwrite the cues).
2. It backs up `mixxxdb.sqlite` first, as `mixxxdb.sqlite.hsb-backup-<timestamp>` next to it. To undo, close
   Mixxx and copy the backup back.
3. Everything is written in one transaction and rolled back on any error (e.g. a slot that's no longer empty).
4. Your cues are never changed: hot cues only go into empty slots (default slots 1–8, settable), a section that
   already has one of your hot cues within 0.25 s is skipped, and only cues this tool made (label `◆ …`, ids
   remembered in the analysis DB) are ever replaced, and only with *Replace cues from my earlier exports*.
   Mixxx's analyzer sets only an intro *start* and an outro *end*; by default the export fills in the missing
   intro end / outro start and keeps yours (*Settings → Phrase analysis* turns that off). Complete markers are
   left alone.
5. The dry run reads and computes only.

Verified against this library (Mixxx 2.4, 2026-10-08): `cues(id, track_id, type, position, length, hotcue,
label, color)`; type 1 hot cue, 2 main cue, 4 loop, 6 intro, 7 outro, 8 audible sound; positions in stereo
samples (seconds × samplerate × 2); `hotcue` 0-based; colors `0xRRGGBB`. On a copy of the library, detected drops
landed within 0.25 s of hot cues placed by hand.

## Hard guarantees

- Mixxx's database is opened with `mode=ro` once per snapshot, copied via SQLite's backup API and
  closed; all queries run on the copy. A test asserts the original file is byte-identical afterwards.
  The one exception is cue export, which you ask for explicitly (see *Cue export safety*).
- Phrase analysis results live in the app's own `analysis.sqlite`, never in Mixxx's database.
- Pure logic (`core/`) has no Qt imports and is fully unit-tested.

## Layout

```
harmonic_set_builder/
  core/      camelot, bpm, energy, ranking, route, setlist, settings, track,
             theory (scales, shared notes, explanations), synth (reference audio), quiz   (no Qt)
  analysis/  grid (Mixxx beat grids), energy (per-bar features, builds), structure (built-in + allin1),
             labels (DJ labels, cleanup), store (analysis.sqlite), pipeline, batch, transitions   (no Qt)
  data/      mixxx_db (locate/snapshot/read), mixxx_cues (cue export), store (autosave), export, config, paths
  ui/        PySide6 models, views, energy graph, sources tree, controller, main window,
             phrases (Phrases view), learn + wheel (Learn view)
  cli.py     the `hsb` command
  tests/
```

## Decisions and deviations from the brief

Defaults from section 9 of the brief are used unless noted.

- **Energy deltas are calibrated to camelotwheel.org** (read from its `app.js`, 2026-10-07; recorded in
  `tests/fixtures/camelotwheel_energy.json`). That site scores relative major/minor and diagonals as **0**,
  ±2 as **±3** and a semitone up (+7 wheel steps) as **+2**, so those replace the brief's table values.
  Semitone down (not offered on the site) mirrors it at −2. All deltas are editable in *Settings → Energy & moves*.
  Note: the site labels +7 wheel steps "Tritone Jump", but it is musically a semitone; this app's
  *Tritone* is the real one (±6 steps) and is a clash.
- Diagonals count in all four directions: besides the brief's A n→B n+1 and B n→A n−1, the
  A n→B n−1 (*Diagonal down (to major)*, "Settle + brighten") and B n→A n+1 (*Diagonal up (to minor)*,
  "Lift + darken") moves share six of seven notes too and are Smooth, energy 0 as on camelotwheel.org.
- Moves the site allows but the brief's table does not (±3 "parallel", +4 "related", A n→B n−3
  "compatible tone") are **off-key clashes** here, per the brief.
- Mixxx specifics confirmed against a real library: `key_id` 1–12 = C…B major, 13–24 = C…B minor;
  `library.location` is a `track_locations.id`; `Playlists.hidden` 0 = normal, 1 = Auto DJ,
  2 = history, −1 = internal placeholder (never shown).
- Crates have no order; imported crates use artist, title (Mixxx's default sort).
- Fix mode shows the **＋** in a narrow column on the red row itself rather than in the gap above it.
- In slot view, a setlist row dragged onto the slot is always **moved**; the brief's exception
  ("unless Hide tracks already in the set is checked") was ambiguous and is not implemented.
- Undo uses whole-set snapshots per command (sets are small), so every mutation is undoable uniformly.
- A route that becomes unreachable stays open showing only the target placeholder, with a hint of which
  rule to relax in the status bar.
- Not in v1 (per the brief): set-length target, live OSC link. The "guess first" learning mode is now the
  Learn view's quiz.

### Phrase analysis spec: decisions

- Module layout follows this repo: cue export is `data/mixxx_cues.py` (not `mixxx/cues.py`), and Mixxx reads
  reuse `data/mixxx_db.py` (no separate `mixxx/db.py`).
- Two analyzers instead of allin1 only, since allin1 can't install on Python 3.14 and is slow on CPU. Both
  produce allin1's label vocabulary so one mapping turns them into DJ labels.
- Label mapping tuned on allin1's real output for a synthetic dance track (`tests/test_analysis.py`): allin1
  found the boundaries but called the first half "intro", so *intro* only counts for a leading run that ends at
  the first drop-level section, and *outro* only after the last drop; elsewhere energy and the kick decide.
  A quiet section that ends the track after its last drop is the Outro even without a kick.
- Build-ups need two of the three rises (onsets, highs, brightness) rather than all three, a falling or absent
  low end, a step up where the build starts, and a jump in energy *or* low end at the drop (a riser can be as
  loud as the drop). An Intro is never relabeled as a Build as a whole.
- Sections shorter than 4 bars (except builds) merge into a neighbour.
- Boundaries snap to the nearest downbeat, then onto the 8-bar grid when within 1 bar (spec §3.5).
- "Nothing runs while Mixxx is open": cue export refuses outright; analysis asks (GUI) or needs
  `--allow-while-mixxx-runs` (CLI), and always runs at nice 10.
- Hot cue colors come from Mixxx's default palette: Intro green, Build yellow, Drop red, Breakdown blue,
  Groove purple, Outro celeste. Default cap 8 slots; with more sections than free slots, Drops, Breakdowns and
  the Outro win.
- The spec's milestone 1 (validate ~20 tracks by ear) is `hsb validate`; label thresholds are constants at the
  top of `analysis/labels.py` for tuning afterwards.
