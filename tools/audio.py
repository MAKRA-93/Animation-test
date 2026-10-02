#!/usr/bin/env python3
"""Beat-grid analysis, UI sound design and the loop mix — numpy + ffmpeg only.

  python3 tools/audio.py --song music/song.mp3 --cues build/sfx.json --out build/audio.wav
  python3 tools/audio.py --placeholder music/placeholder.wav     # synth a 120 BPM groove

The song is analysed for tempo, beat phase and downbeats, conformed to 120 BPM if it is
slightly off, and cut on a downbeat for exactly LOOP seconds with a seamless wrap.
Every UI sound is placed so its measured peak lands on its cue time.
"""
import argparse, json, subprocess, sys
import numpy as np

SR = 48000
rng = np.random.default_rng(7)


# ───────────────────────── io ─────────────────────────
def load(path, tempo=1.0):
    af = ['-af', f'atempo={tempo:.6f}'] if abs(tempo - 1) > 1e-6 else []
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, *af, '-f', 'f32le', '-ac', '2', '-ar', str(SR), '-'],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2).astype(np.float64)


def save(path, x):
    x = np.clip(x, -1, 1)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'f32le', '-ac', str(x.shape[1]), '-ar', str(SR), '-i', '-', path],
                   input=x.astype(np.float32).tobytes(), check=True)


# ───────────────────────── analysis ─────────────────────────
HOP, NFFT = 240, 2048                       # 5 ms hop → 200 frames/s
FPS = SR / HOP


def onset_env(x):
    m = x.mean(1)
    n = 1 + (len(m) - NFFT) // HOP
    idx = np.arange(NFFT)[None, :] + HOP * np.arange(n)[:, None]
    spec = np.abs(np.fft.rfft(m[idx] * np.hanning(NFFT), axis=1))
    logm = np.log1p(1000 * spec)
    flux = np.maximum(0, np.diff(logm, axis=0, prepend=logm[:1]))
    f = np.fft.rfftfreq(NFFT, 1 / SR)
    full = flux.sum(1)
    low = flux[:, f < 160].sum(1)
    norm = lambda e: (e - e.mean()) / (e.std() + 1e-9)
    # frames are stamped at the window centre
    return norm(full), norm(low), (np.arange(n) * HOP + NFFT / 2) / SR


def analyse(x, bpm_hint=120):
    env, low, times = onset_env(x)
    e = np.maximum(env, 0)
    # tempo: autocorrelation, weighted towards the hint on a log-tempo scale
    ac = np.correlate(e, e, 'full')[len(e) - 1:]
    lags = np.arange(len(ac))
    bpm_of = lambda lag: 60 * FPS / np.maximum(lag, 1)
    ok = (bpm_of(lags) > 70) & (bpm_of(lags) < 200)
    score = np.where(ok, ac * np.exp(-0.5 * (np.log2(bpm_of(lags) / bpm_hint) / 0.35) ** 2), 0)
    L = int(np.argmax(score))
    a, b, c = ac[L - 1], ac[L], ac[L + 1]
    period = L + 0.5 * (a - c) / (a - 2 * b + c)                 # parabolic refine, frames
    # phase: comb over the envelope
    phases = np.arange(0, period, 0.25)
    k = np.arange(int((len(e) - period) / period))
    comb = [e[np.clip(np.round(p + k * period).astype(int), 0, len(e) - 1)].sum() for p in phases]
    phase = phases[int(np.argmax(comb))]
    # refine: snap each predicted beat to its local onset peak, then least-squares the grid
    pred = phase + k * period
    snapped = []
    for p in pred:
        i0, i1 = int(p) - 6, int(p) + 7
        if i0 < 0 or i1 >= len(e): continue
        seg = e[i0:i1]
        if seg.max() > 1.0: snapped.append((p, i0 + np.argmax(seg)))
    snapped = np.array(snapped)
    n = np.round((snapped[:, 0] - phase) / period)
    A = np.vstack([n, np.ones_like(n)]).T
    per, off = np.linalg.lstsq(A, snapped[:, 1], rcond=None)[0]
    beat_t = (off + per * np.arange(int((len(e) - off) / per))) * HOP / SR + NFFT / 2 / SR
    beat_t = beat_t[beat_t >= 0]
    # downbeat: the beat-in-bar position with the most kick energy
    lowv = np.interp(beat_t, times, np.maximum(low, 0))
    db = int(np.argmax([lowv[j::4].sum() for j in range(4)]))
    resid = (snapped[:, 1] - (off + per * n)) * HOP / SR * 1000
    beat_t = beat_t + transient_bias(x, beat_t)
    return dict(bpm=60 * FPS / per, beats=beat_t, downbeat_index=db, jitter_ms=float(np.abs(resid).mean()))


def transient_bias(x, beats, win=0.035):
    """STFT frames smear onsets by ~half a window; find the real attack in the waveform
    (steepest rise of a 1 ms energy envelope near each beat) and return the median offset."""
    m = np.abs(np.diff(x.mean(1), prepend=0))                 # crude high-pass keeps attacks sharp
    k = int(0.001 * SR)
    envl = np.convolve(m, np.ones(k) / k, 'same')
    rise = np.diff(envl, prepend=0)
    offs = []
    for t in beats:
        a, b = int((t - win) * SR), int((t + win) * SR)
        if a < 0 or b >= len(rise): continue
        offs.append((a + np.argmax(rise[a:b])) / SR - t)
    return float(np.median(offs)) if offs else 0.0


def pick_start(x, beats, db, loop, preroll=0.05):
    """The downbeat whose LOOP-second window is loudest (skips quiet intros)."""
    best, best_e = None, -1
    for t in beats[db::4]:
        if t < preroll or t + loop > len(x) / SR - 0.01: continue
        seg = x[int(t * SR):int((t + loop) * SR)]
        en = float(np.sqrt((seg ** 2).mean()))
        if en > best_e: best, best_e = t, en
    return best


# ───────────────────────── sound design ─────────────────────────
def env_exp(n, tau):
    return np.exp(-np.arange(n) / (tau * SR))


def tone(f, dur, tau, f_end=None):
    n = int(dur * SR)
    fr = np.full(n, f) if f_end is None else f * (f_end / f) ** (np.arange(n) / n)
    return np.sin(TAU * np.cumsum(fr) / SR) * env_exp(n, tau)


def hp(x, a=0.9):                            # one-pole high-pass
    y = np.zeros_like(x); prev = 0.0
    for i in range(len(x)):
        y[i] = a * (y[i - 1] if i else 0) + x[i] - prev; prev = x[i]
    return y


def noise(dur, tau, hpa=0.95):
    n = int(dur * SR)
    return hp(rng.standard_normal(n), hpa) * env_exp(n, tau)


def fit(*parts):
    n = max(len(p) for p in parts)
    return sum(np.pad(p, (0, n - len(p))) for p in parts)


TAU = 2 * np.pi
SOUNDS = {
    'down':    lambda: fit(0.55 * tone(2300, .05, .006), 0.35 * tone(4600, .03, .003), 0.18 * noise(.02, .0025), 0.5 * tone(190, .06, .014, 150)),
    'up':      lambda: fit(0.35 * tone(3100, .04, .004), 0.12 * noise(.015, .002), 0.22 * tone(240, .04, .008, 200)),
    'key':     lambda: fit(0.25 * noise(.03, .004, .9), 0.3 * tone(2800, .03, .004), 0.25 * tone(320, .04, .01, 260)),
    'enter':   lambda: fit(0.32 * noise(.04, .006, .9), 0.35 * tone(2200, .04, .006), 0.6 * tone(150, .09, .025, 110)),
    'toggle':  lambda: fit(0.7 * tone(560, .08, .018, 300), 0.25 * tone(2600, .03, .004), 0.12 * noise(.02, .003)),
    'success': lambda: fit(0.32 * tone(1318.5, .5, .12), np.pad(0.28 * tone(1975.5, .6, .16), (int(.075 * SR), 0))),
    'limit':   lambda: fit(0.6 * tone(150, .12, .03, 120), 0.12 * tone(1800, .03, .005)),
}
GAIN = {'down': 0.55, 'up': 0.4, 'key': 0.45, 'enter': 0.55, 'toggle': 0.55, 'success': 0.22, 'limit': 0.5}


def place_cues(cues, loop):
    out = np.zeros((int(round(loop * SR)), 2))
    bank = {k: f() for k, f in SOUNDS.items()}
    report = []
    for c in cues:
        s = bank[c['s']] * GAIN[c['s']]
        peak = int(np.argmax(np.abs(s)))                       # measured peak
        start = int(round(c['t'] * SR)) - peak
        idx = (start + np.arange(len(s))) % len(out)            # wraps across the loop seam
        pan = {'key': 0.1, 'toggle': -0.05}.get(c['s'], 0.0)
        out[idx, 0] += s * (1 - pan); out[idx, 1] += s * (1 + pan)
        report.append(f"{c['s']:>8} cue {c['t']:7.3f}s  peak +{peak / SR * 1000:5.1f}ms")
    return out, report


# ───────────────────────── placeholder groove (until the real song is in) ─────────────────────────
def placeholder(path, bpm=120, bars=16, pickup=0.37):
    beat = 60 / bpm
    n = int((pickup + bars * 4 * beat + 1) * SR)
    x = np.zeros(n)
    def put(sig, t, g=1.0):
        i = int(t * SR); m = min(len(sig), n - i); x[i:i + m] += g * sig[:m]
    kick = lambda: fit(tone(110, .3, .07, 42), 0.3 * noise(.006, .001))
    clap = lambda: fit(*(np.pad(noise(.18, .045, .97), (int(d * SR), 0)) for d in (0, .009, .018)))
    hat = lambda: noise(.05, .008, .995)
    roots = [55.0, 55.0, 43.65, 49.0]             # A A F G
    for b in range(bars * 4):
        t = pickup + b * beat; bar = b // 4
        if bar >= 1:                              # one bar of hats only, so the first strong kick is not t=0
            put(kick(), t, 0.9)
            if b % 2 == 1: put(clap(), t, 0.35)
            r = roots[bar % 4]
            put(tone(r, beat * .9, .25) + 0.3 * tone(2 * r, beat * .9, .12), t + beat / 2, 0.35)
        put(hat(), t + beat / 2, 0.25); put(hat(), t, 0.1)
        if bar >= 2 and b % 4 == 2:
            for f in (440, 523.25, 659.25):
                put(tone(f, .4, .14), t + beat / 2, 0.06)
    x = np.tanh(1.2 * x) * 0.7
    save(path, np.stack([x, x], 1))
    print('placeholder groove →', path)


# ───────────────────────── main ─────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--song'); ap.add_argument('--cues'); ap.add_argument('--out')
    ap.add_argument('--placeholder')
    ap.add_argument('--bpm', type=float, default=120)
    a = ap.parse_args()
    if a.placeholder: return placeholder(a.placeholder)

    meta = json.load(open(a.cues)); loop = meta['loop']
    first = analyse(load(a.song), a.bpm)
    tempo = a.bpm / first['bpm']
    if abs(tempo - 1) > 0.0015:                                # conform to 120 if a little off
        if abs(tempo - 1) > 0.08: sys.exit(f"song is {first['bpm']:.2f} BPM — too far from {a.bpm}")
        x = load(a.song, tempo); res = analyse(x, a.bpm)
    else:
        x, res = load(a.song), first
    print(f"tempo {first['bpm']:.2f} BPM → conformed ×{tempo:.4f} → {res['bpm']:.2f} BPM,"
          f" beat jitter {res['jitter_ms']:.1f} ms, downbeat = beat {res['downbeat_index']} of the grid")
    start = pick_start(x, res['beats'], res['downbeat_index'], loop)
    print(f'loop starts on the downbeat at {start:.3f}s')

    N = int(round(loop * SR)); s0 = int(round(start * SR))
    song = x[s0:s0 + N].copy()
    xf = int(0.04 * SR)                                        # seam: fade the cut tail into the pre-roll
    ramp = np.linspace(0, 1, xf)[:, None]
    song[-xf:] = song[-xf:] * (1 - ramp) + x[s0 - xf:s0] * ramp
    song *= 0.2 / (np.sqrt((song ** 2).mean()) + 1e-9)         # ~-14 dBFS RMS

    sfx, report = place_cues(meta['cues'], loop)
    print('\n'.join(report))
    chk = analyse(song, a.bpm)                                  # grid check on the cut, before the SFX
    err = (chk['beats'] - np.round(chk['beats'] / 0.5) * 0.5) * 1000
    print(f'cut: {chk["bpm"]:.2f} BPM, beats vs the 0.5 s grid: median {np.median(err):+.1f} ms, max |{np.abs(err).max():.1f}| ms')
    mix = song + sfx
    pk = np.abs(mix).max()
    if pk > 0.89: mix *= 0.89 / pk                              # -1 dBFS ceiling
    save(a.out, mix)
    print('→', a.out)


if __name__ == '__main__':
    main()
