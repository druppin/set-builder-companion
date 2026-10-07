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

- **Sources** (right): click Library, a crate or a playlist to focus it. Right-click → *Open as set*
  loads a Mixxx playlist/crate as a new set (also works as an analyzer for existing sets).
- **Track table** (middle): Mixxx's columns plus Move / Mood / Energy Δ / BPM Δ / Tier / ½·2×
  against the reference track (selected setlist row, else the last track). Default order is the
  suggestion ranking; *Suggested order* returns to it. Right-click the header to choose columns.
- **Setlist** (left): drag tracks in, drag rows to reorder, Delete to remove, double-click a track to
  append. Red = key clash with the previous track, amber = duplicate. Undo/redo covers everything.
- **To be added**: tracks you want to play later. Right-click (or ⋯) → *Suggest where to place it*
  or *Show me how to get here*, which opens a route of dashed transitional entries. Click the first
  one to see its candidates; drop or double-click a track to fill it.
- **Help me fix key mixing mistakes**: shows **＋** on red rows; click it to bridge the clash.
- **Energy graph** (top): energy from Camelot moves, breaking at clashes; click a point to select the row.

Sets autosave (debounced 0.5 s, atomic, last 20 versions) to the app data directory
(`~/.local/share/HarmonicSetBuilder/Harmonic Set Builder/sets` on Linux). Config is one JSON file in
the app config directory.

## Hard guarantees

- Mixxx's database is opened with `mode=ro` once per snapshot, copied via SQLite's backup API and
  closed; all queries run on the copy. A test asserts the original file is byte-identical afterwards.
- Pure logic (`core/`) has no Qt imports and is fully unit-tested.

## Layout

```
harmonic_set_builder/
  core/   camelot, bpm, energy, ranking, route, setlist, settings, track   (no Qt)
  data/   mixxx_db (locate/snapshot/read), store (autosave), export (.m3u8/.txt), config
  ui/     PySide6 models, views, energy graph, sources tree, controller, main window
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
- Dragging a pool track into the setlist removes it from To be added (it has been added).
- In slot view, a setlist row dragged onto the slot is always **moved**; the brief's exception
  ("unless Hide tracks already in the set is checked") was ambiguous and is not implemented.
- Undo uses whole-set snapshots per command (sets are small), so every mutation is undoable uniformly.
- A route that becomes unreachable stays open showing only the target placeholder, with a hint of which
  rule to relax in the status bar.
- Not in v1 (per the brief): audio preview, set-length target, "guess first" learning mode, live OSC link.
