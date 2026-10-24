"""Procedurally generated sound effects for the explainer Reels (no samples, no licences).

sfx("riser+hit") -> mono float32 at 44.1 kHz. Names (combine with "+", or use words that contain them):
whoosh, hit, glitch, pop, tick, ding, riser, rewind, zap, alarm, crackle, hum, buzzer, click, sparkle,
hiss, bubble, snap, wind. Anything unknown falls back to a whoosh.
"""

import numpy as np

SR = 44100


def _t(seconds):
    return np.arange(int(SR * seconds)) / SR


def _env(n, attack=0.01, release=0.2):
    t = np.arange(n) / SR
    a = np.clip(t / max(attack, 1e-4), 0, 1)
    r = np.clip((t[-1] - t) / max(release, 1e-4), 0, 1) if n else t
    return a * r


def _lowpass(x, k):
    k = max(1, int(k))
    return np.convolve(x, np.ones(k) / k, mode="same")


def _sweep(f0, f1, seconds):
    t = _t(seconds)
    f = np.geomspace(f0, f1, len(t))
    return np.sin(2 * np.pi * np.cumsum(f) / SR)


def whoosh(rng, seconds=0.6):
    n = int(SR * seconds)
    noise = rng.uniform(-1, 1, n)
    # band-limited noise whose brightness rises then falls
    out = np.zeros(n)
    for k, w in ((24, 0.6), (8, 0.4)):
        out += _lowpass(noise, k) * w
    shape = np.sin(np.linspace(0, np.pi, n)) ** 2
    return out * shape * 1.6


def hit(rng):
    t = _t(0.6)
    body = np.sin(2 * np.pi * np.cumsum(np.geomspace(90, 38, len(t))) / SR) * np.exp(-t * 7)
    click = rng.uniform(-1, 1, len(t)) * np.exp(-t * 80) * 0.5
    return body + click


def glitch(rng):
    out = []
    for _ in range(7):
        d = rng.uniform(0.02, 0.06)
        t = _t(d)
        f = rng.uniform(200, 1800)
        sq = np.sign(np.sin(2 * np.pi * f * t))
        crushed = np.round(sq * 4) / 4 * rng.uniform(0.4, 1.0)
        out.append(crushed)
        out.append(np.zeros(int(SR * rng.uniform(0.0, 0.02))))
    return np.concatenate(out) * 0.5


def pop(rng):
    t = _t(0.09)
    return _sweep(900, 320, 0.09) * np.exp(-t * 45)


def tick(rng, count=6, spacing=0.09):
    out = np.zeros(int(SR * spacing * count + SR * 0.05))
    for k in range(count):
        i = int(SR * spacing * k)
        t = _t(0.02)
        out[i:i + len(t)] += np.sin(2 * np.pi * 2400 * t) * np.exp(-t * 300)
    return out * 0.7


def ding(rng):
    t = _t(1.0)
    return (np.sin(2 * np.pi * 1318 * t) + 0.5 * np.sin(2 * np.pi * 2637 * t)) * np.exp(-t * 4) * 0.6


def riser(rng):
    n = int(SR * 0.8)
    noise = _lowpass(rng.uniform(-1, 1, n), 6)
    tone = _sweep(180, 1400, 0.8)
    ramp = np.linspace(0, 1, n) ** 2
    return (noise * 0.5 + tone * 0.4) * ramp


def rewind(rng):
    n = int(SR * 0.6)
    tone = _sweep(1600, 160, 0.6)
    trem = 0.6 + 0.4 * np.sin(2 * np.pi * 28 * np.arange(n) / SR)
    return tone * trem * _env(n, 0.02, 0.15) * 0.6


def zap(rng):
    t = _t(0.25)
    return _sweep(2200, 180, 0.25) * np.exp(-t * 10) * 0.7


def alarm(rng):
    out = []
    for f in (880, 660, 880):
        t = _t(0.13)
        out.append(np.sign(np.sin(2 * np.pi * f * t)) * 0.35)
    return np.concatenate(out)


def crackle(rng):
    n = int(SR * 0.5)
    out = np.zeros(n)
    for i in rng.integers(0, n - 200, 40):
        out[i:i + 60] += rng.uniform(-1, 1, 60) * np.exp(-np.arange(60) / 12)
    return out * 0.8


def hum(rng):
    t = _t(0.9)
    return (np.sin(2 * np.pi * 110 * t) + 0.4 * np.sin(2 * np.pi * 220 * t)) * np.sin(np.pi * t / t[-1]) * 0.5


def buzzer(rng):
    t = _t(0.35)
    return np.sign(np.sin(2 * np.pi * 120 * t)) * _env(len(t), 0.005, 0.05) * 0.4


def click(rng):
    t = _t(0.04)
    return rng.uniform(-1, 1, len(t)) * np.exp(-t * 150)


def sparkle(rng):
    out = np.zeros(int(SR * 0.6))
    for _ in range(14):
        i = int(rng.uniform(0, 0.5) * SR)
        t = _t(0.06)
        out[i:i + len(t)] += np.sin(2 * np.pi * rng.uniform(2500, 5000) * t) * np.exp(-t * 60) * 0.35
    return out


def hiss(rng):
    n = int(SR * 0.5)
    return rng.uniform(-1, 1, n) * np.linspace(1, 0, n) ** 2 * 0.4


def bubble(rng):
    out = np.zeros(int(SR * 0.6))
    for _ in range(9):
        i = int(rng.uniform(0, 0.5) * SR)
        out[i:i + int(SR * 0.05)] += _sweep(300, 900, 0.05) * 0.35
    return out


SOUNDS = dict(whoosh=whoosh, hit=hit, glitch=glitch, pop=pop, tick=tick, ding=ding, riser=riser,
              rewind=rewind, zap=zap, alarm=alarm, crackle=crackle, hum=hum, buzzer=buzzer, click=click,
              sparkle=sparkle, hiss=hiss, bubble=bubble, snap=click, wind=lambda r: whoosh(r, 1.2),
              impact=hit, boom=hit, swoosh=whoosh, shutter=click, beep=pop, chime=ding, laser=zap)


def sfx(names, seed=0):
    """Mix one or more named effects ("riser+hit": the hit lands at the end of the riser)."""
    rng = np.random.default_rng(seed)
    parts = []
    for raw in str(names or "whoosh").lower().replace(",", "+").split("+"):
        key = next((k for k in SOUNDS if k in raw.strip()), "whoosh")
        parts.append(SOUNDS[key](rng))
    # play the parts one after another, overlapping a little
    total = sum(len(p) for p in parts)
    out = np.zeros(total)
    i = 0
    for p in parts:
        out[i:i + len(p)] += p
        i += int(len(p) * 0.85)
    peak = np.abs(out).max()
    return (out / peak * 0.9 if peak > 0 else out).astype(np.float32)
