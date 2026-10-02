#!/usr/bin/env bash
# Full pipeline: build → beat stills → cues → audio → 60 fps motion-blurred video → mux.
#   ./render.sh [song.mp3]     (no song: a synthesized 120 BPM placeholder groove)
set -euo pipefail
cd "$(dirname "$0")"
export NODE_PATH="${NODE_PATH:-$(npm root -g)}"
SONG="${1:-music/placeholder.wav}"
mkdir -p build music
[ -f "$SONG" ] || python3 tools/audio.py --placeholder "$SONG"
python3 tools/build.py
node tools/render.cjs stills build/beats                      # one frame per beat, for review
ffmpeg -y -v error -pattern_type glob -i 'build/beats/t*.png' -vf "scale=360:360,tile=7x4:padding=4:color=white" -frames:v 1 build/beats.png
node tools/render.cjs sfx build/sfx.json
python3 tools/audio.py --song "$SONG" --cues build/sfx.json --out build/audio.wav
node tools/render.cjs video build/video.mp4
ffmpeg -y -v error -i build/video.mp4 -i build/audio.wav -c:v copy -c:a aac -b:a 256k -shortest -movflags +faststart shape-morph.mp4
ffmpeg -y -v error -i build/audio.wav -c:a aac -b:a 192k loop.m4a
echo "→ shape-morph.mp4"
