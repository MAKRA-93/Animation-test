# Shape Morph

One shape, never cut. Over a 14 s loop (7 bars at 120 BPM) it becomes a button, a loader, a check, a dynamic island, a music player, a scrub bar, a volume slider, a toggle, liquid tabs, a chart, ⌘K, a toast, and then the button again.

- `index.html`: the whole scene in one file, 1440×1440, with Geist inlined. Open it to preview (click to start the audio) or add `?t=6.5` to freeze one moment.
- `shape-morph.mp4`: 60 fps render with 4-subframe motion blur and the audio mix.

## How it works
- `seek(t)` computes every style from `t`. There are no CSS transitions, no timers, and no state carried between frames.
- Springs are closed-form step responses. A value that changes target many times is the sum of one spring per change. Periodic tracks also add the previous loop's springs, so the last frame matches the first in both position and velocity.
- The edges of the toggle knob and the tab indicator ride separate springs. The leading edge is stiff and the trailing edge is soft.
- Drags are direct manipulation: while the cursor is held, the value comes from its position. On release, the rubber-band springs back from its release position and velocity.
- `tools/audio.py` analyses the song with numpy: spectral-flux tempo, comb-filter phase, kick-weighted downbeat, and waveform transient refinement. It conforms the song to 120 BPM, cuts it on the loudest downbeat with a seamless wrap, and places each UI sound by its measured peak.

## Render
```sh
./render.sh                 # placeholder groove
./render.sh music/song.mp3  # your track (~120 BPM)
```
Requirements: Node with Playwright (Chromium), Python 3 with numpy, and ffmpeg. The review frames go to `build/beats.png`.
