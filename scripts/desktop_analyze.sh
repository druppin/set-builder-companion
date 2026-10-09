#!/usr/bin/env bash
# Analyze the library on the desktop GPU (WSL2) and export the results to the music drive.
#   bash scripts/desktop_analyze.sh [DRIVE_LETTER] [extra hsb analyze options, e.g. --limit 50]
# Then on the laptop: Phrases view → Import…, or hsb import-analysis /run/media/druppin/A861-EA64/hsb-analysis.json.gz
set -euo pipefail
LETTER="${1:-d}"; shift || true
DRIVE="/mnt/$LETTER"
LAPTOP_ROOT=/run/media/druppin/A861-EA64
[ -d "$DRIVE/Music" ] || { echo "No Music folder in $DRIVE (mount it: sudo mkdir -p $DRIVE && sudo mount -t drvfs ${LETTER^^}: $DRIVE)"; exit 1; }
[ -f "$DRIVE/mixxxdb.sqlite" ] || { echo "Copy the laptop's ~/.mixxx/mixxxdb.sqlite to the drive first"; exit 1; }
if [ ! -e "$LAPTOP_ROOT" ]; then sudo mkdir -p "$(dirname "$LAPTOP_ROOT")"; sudo ln -s "$DRIVE" "$LAPTOP_ROOT"; fi
.venv/bin/hsb --mixxx-db "$DRIVE/mixxxdb.sqlite" analyze --backend allin1 --compare raveform,cuedetr \
  --allow-while-mixxx-runs "$@"
.venv/bin/hsb export-analysis "$DRIVE/hsb-analysis.json.gz"
