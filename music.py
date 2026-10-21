"""Soundtracks matched to each place's mood.

If music/<mood>/ (or music/) holds your own royalty-free tracks (.mp3 .m4a .wav), one of them is
used. Otherwise a Turkish-flavoured track is generated: saz-like plucked strings on a makam
scale, a drone/pad underneath and darbuka-style percussion, so no music licence is ever needed.

Moods: calm (drying lakes), water (dams, deltas, irrigation), city (growing cities),
build (airports, bridges, ports), drama (fires, mines, drowned towns), mountain.
"""

import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent
SR = 44100
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav"}

SCALES = {                                   # semitones from the tonic
    "hicaz":   [0, 1, 4, 5, 7, 8, 10, 12],
    "kurdi":   [0, 1, 3, 5, 7, 8, 10, 12],
    "huseyni": [0, 2, 3, 5, 7, 9, 10, 12],
    "rast":    [0, 2, 4, 5, 7, 9, 11, 12],  # rast without its microtones
}

MOODS = {
    "calm":     dict(scale="kurdi",   tonic=62, bpm=72,  perc=None,      density=0.45),
    "water":    dict(scale="huseyni", tonic=62, bpm=84,  perc="soft",    density=0.6),
    "city":     dict(scale="hicaz",   tonic=62, bpm=100, perc="maqsum",  density=0.65),
    "build":    dict(scale="rast",    tonic=60, bpm=112, perc="drive",   density=0.7),
    "drama":    dict(scale="hicaz",   tonic=57, bpm=70,  perc="toms",    density=0.4),
    "mountain": dict(scale="huseyni", tonic=64, bpm=66,  perc=None,      density=0.35),
}


def own_track(mood, seed):
    for folder in (ROOT / "music" / mood, ROOT / "music"):
        if folder.is_dir():
            tracks = sorted(p for p in folder.iterdir() if p.suffix.lower() in AUDIO_EXT)
            if tracks:
                return tracks[seed % len(tracks)]
    return None


def soundtrack(mood, seconds, seed, tmp_dir):
    """Path to an audio file for this mood: your own track if present, else a generated one."""
    track = own_track(mood, seed)
    if track:
        return track
    path = Path(tmp_dir) / "soundtrack.wav"
    write_wav(path, generate(mood, seconds, seed))
    return path


# ---------------------------------------------------------------- instruments

def midi_hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def pluck(freq, dur, rng, decay=0.996):
    """Karplus-Strong string, doubled and slightly detuned like a saz course."""
    n = int(SR * dur)
    out = np.zeros(n)
    for detune in (1.0, 1.004):
        period = max(2, int(SR / (freq * detune)))
        y = np.zeros(n + period + 1)                     # y[0] stays 0 so i - period - 1 is never negative
        y[1:period + 1] = np.convolve(rng.uniform(-1, 1, period), [0.5, 0.5], "same")  # softer attack
        for i in range(period + 1, n + period + 1, period):   # vectorised one period at a time
            j = min(i + period, n + period + 1)
            y[i:j] = decay * 0.5 * (y[i - period:j - period] + y[i - period - 1:j - period - 1])
        out += y[period + 1:period + 1 + n]
    return out * 0.5


def pad(freqs, n, rng):
    t = np.arange(n) / SR
    out = np.zeros(n)
    for f in freqs:
        swell = 0.7 + 0.3 * np.sin(2 * np.pi * rng.uniform(0.05, 0.15) * t + rng.uniform(0, 6.3))
        for h, amp in ((1, 1.0), (2, 0.3), (3, 0.12)):
            out += amp * np.sin(2 * np.pi * f * h * t + rng.uniform(0, 6.3)) * swell
    return out / max(1, len(freqs))


def doum(dur=0.35):
    t = np.arange(int(SR * dur)) / SR
    f = 55 + 70 * np.exp(-t * 25)                  # pitch drops like a darbuka centre hit
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9)


def tek(rng, dur=0.07, bright=1.0):
    t = np.arange(int(SR * dur)) / SR
    noise = np.diff(rng.uniform(-1, 1, len(t) + 1))   # high-passed noise: rim click
    ring = np.sin(2 * np.pi * 900 * t) * 0.4
    return (noise * bright + ring) * np.exp(-t * 60)


RHYTHMS = {   # one bar of 8 eighth notes: D = doum, T = tek, k = soft tek
    "maqsum": "DT.TD.T.",
    "soft":   "D...k...",
    "drive":  "DkTkDkTk",
    "toms":   "D.....D.",
}


# ---------------------------------------------------------------- composition

def melody(rng, scale, bars, density):
    """Phrases of 2 bars, arranged A A' B A so they sound composed rather than random."""
    def phrase():
        notes, beat, deg = [], 0.0, int(rng.integers(0, 5))
        while beat < 8:
            dur = float(rng.choice([0.5, 0.5, 1, 1, 1.5, 2]))
            dur = min(dur, 8 - beat)
            if rng.random() < density or beat == 0:
                deg = int(np.clip(deg + rng.choice([-2, -1, -1, 0, 1, 1, 2]), 0, len(scale) - 1))
                notes.append((beat, dur, deg))
            beat += dur
        last_beat, last_dur, _ = notes[-1]
        notes[-1] = (last_beat, last_dur, int(rng.choice([0, 4])))   # land on tonic or fifth
        return notes

    a, b = phrase(), phrase()
    a2 = [(bt, d, min(len(scale) - 1, dg + (1 if i == len(a) // 2 else 0))) for i, (bt, d, dg) in enumerate(a)]
    form = [a, a2, b, a]
    out = []
    for p in range((bars + 1) // 2):
        for bt, d, dg in form[p % 4]:
            out.append((p * 8 + bt, d, dg))
    return out


def generate(mood, seconds, seed):
    cfg = MOODS.get(mood, MOODS["calm"])
    rng = np.random.default_rng(seed)
    scale = SCALES[cfg["scale"]]
    beat = 60 / cfg["bpm"]
    n = int(SR * (seconds + 1))
    left, right = np.zeros(n), np.zeros(n)

    def add(sig, at, gain, pan=0.0):
        i = int(at * SR)
        if i >= n:
            return
        sig = sig[: n - i] * gain
        left[i:i + len(sig)] += sig * (1 - pan) / 2 * 2 ** 0.5
        right[i:i + len(sig)] += sig * (1 + pan) / 2 * 2 ** 0.5

    tonic = cfg["tonic"]
    # drone + pad on tonic and fifth, one octave down
    bed = pad([midi_hz(tonic - 12), midi_hz(tonic - 5), midi_hz(tonic)], n, rng)
    add(bed, 0, 0.22)

    # melody starts after one bar
    bars = int(seconds / (beat * 4)) + 1
    for start, dur, deg in melody(rng, scale, bars, cfg["density"]):
        at = beat * 4 + start * beat
        if at > seconds - 1.5:
            break
        f = midi_hz(tonic + scale[deg])
        add(pluck(f, min(2.5, dur * beat + 0.8), rng), at, 0.55, pan=rng.uniform(-0.3, 0.3))
        if rng.random() < 0.25:                     # occasional octave answer, like a second saz
            add(pluck(f * 2, 0.8, rng), at + beat / 2, 0.18, pan=0.5)

    # percussion from the second bar
    if cfg["perc"]:
        pattern = RHYTHMS[cfg["perc"]]
        eighth = beat / 2
        hits = {"D": (doum(), 0.6), "T": (tek(rng), 0.35), "k": (tek(rng, bright=0.5), 0.18)}
        t = beat * 4
        while t < seconds - 1.0:
            for i, ch in enumerate(pattern):
                if ch in hits:
                    sig, gain = hits[ch]
                    add(sig, t + i * eighth, gain * rng.uniform(0.85, 1.0), pan=0.1 if ch == "D" else -0.15)
            t += eighth * len(pattern)

    # a little room: two echoes
    for delay, gain in ((0.27, 0.25), (0.54, 0.1)):
        d = int(SR * delay)
        left[d:] += left[:-d] * gain
        right[d:] += right[:-d] * gain

    mix = np.stack([left, right], axis=1)[: int(SR * seconds)]
    t = np.arange(len(mix)) / SR
    env = np.minimum(1, t / 0.8) * np.clip((seconds - t) / 2.0, 0, 1)
    mix *= env[:, None]
    return mix / (np.abs(mix).max() + 1e-9) * 0.8


def write_wav(path, stereo):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((stereo * 32767).astype(np.int16).tobytes())
