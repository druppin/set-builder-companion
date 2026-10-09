#!/usr/bin/env bash
# Run the analyzer benchmark on the desktop (WSL2), on the laptop's music drive and a
# copy of the laptop's Mixxx library, so it picks the same tracks with the same paths.
#
#   bash scripts/desktop_benchmark.sh [DRIVE_LETTER] [extra hsb benchmark options]
#
# DRIVE_LETTER is the music drive's letter in Windows (default d). The drive's top
# level must hold the Music folder and mixxxdb.sqlite (copied there from the laptop's
# ~/.mixxx/ while Mixxx was closed). Results are copied back onto the drive.
set -euo pipefail
LETTER="${1:-d}"
shift || true
DRIVE="/mnt/$LETTER"
LAPTOP_ROOT=/run/media/druppin/A861-EA64  # where the laptop mounts this drive

[ -d "$DRIVE/Music" ] || { echo "No Music folder in $DRIVE (is the drive letter right? see: ls /mnt)"; exit 1; }
[ -f "$DRIVE/mixxxdb.sqlite" ] || { echo "Copy the laptop's ~/.mixxx/mixxxdb.sqlite to the drive first"; exit 1; }

# The library stores laptop paths; make them resolve here too (/run is reset when WSL restarts).
if [ ! -e "$LAPTOP_ROOT" ]; then
  sudo mkdir -p "$(dirname "$LAPTOP_ROOT")"
  sudo ln -s "$DRIVE" "$LAPTOP_ROOT"
fi

.venv/bin/hsb --mixxx-db "$DRIVE/mixxxdb.sqlite" benchmark --sample 10 "$@"

OUT="$HOME/.local/share/HarmonicSetBuilder/Harmonic Set Builder/benchmark/latest.json"
cp "$OUT" "$DRIVE/hsb-benchmark-desktop.json"
cp "${OUT%.json}.html" "$DRIVE/hsb-benchmark-desktop.html"
echo "Copied the results to the drive: hsb-benchmark-desktop.json and hsb-benchmark-desktop.html"
echo "Open the .html in Windows (double-click it on the drive) to see each track's cues."
.venv/bin/hsb export-analysis "$DRIVE/hsb-analysis.json.gz"
echo "To see these tracks in the app: on the laptop, Phrases view → Import…, pick hsb-analysis.json.gz on the drive."
