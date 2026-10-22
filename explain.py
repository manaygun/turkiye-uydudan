#!/usr/bin/env python3
"""Render an animated, narrated explainer Reel (1080x1920) from a short script.

A script is a list of beats. Each beat has narration ("say") and a visual ("show"):
  title    icons in a row                       {"type": "title", "icons": ["📱","📱"]}
  split    an icon breaks into many pieces      {"type": "split", "icon": "🖼"}
  merge    pieces come back together            {"type": "merge", "icon": "🖼"}
  focus    one big icon, pulsing                {"type": "focus", "icon": "✉"}
  flow     packets travel between nodes         {"type": "flow", "nodes": [["📱"], ["📶"]], "style": "light"}
  stat     a number counts up                   {"type": "stat", "value": "0.3", "icon": "⏱"}
  list     icons appear one by one              {"type": "list", "items": [["🔒"], ["🔑"]]}
  compare  two things side by side              {"type": "compare", "left": ["🐢"], "right": ["🐇"]}
Text fields in the script (labels, callouts, units) are not drawn: the voice explains, and the
only text on screen is one subtitle word at a time. Each beat lasts as long as its narration.

  python explain.py --topic photo-to-phone
"""

import argparse
import json
import math
import re
import sys
import tempfile
import wave
from functools import lru_cache
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFont

import music
import tts

ROOT = Path(__file__).parent
W, H, FPS = 1080, 1920, 30
SAFE_L, SAFE_R = 120, W - 120      # tall phones crop the sides
STAGE_CY = 700                      # centre of the animation area
SUB_Y = 1130                        # one subtitle word at a time, above Instagram's caption
HANDLE = "@gecmistenharita"
LOGO_MARK = ROOT / "assets" / "logo_mark.png"
SR = 44100

YELLOW = (255, 214, 92)
CYAN = (90, 210, 255)
WHITE = (240, 244, 250)
GREY = (150, 160, 178)
PANEL = (28, 38, 66)
PALETTE = [(90, 210, 255), (255, 214, 92), (255, 120, 140), (130, 230, 160), (190, 150, 255)]


# ---------------------------------------------------------------- helpers

def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def ease_out(x):
    x = clamp(x)
    return 1 - (1 - x) ** 3


def ease_in_out(x):
    x = clamp(x)
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def appear(t, delay=0.0, d=0.35):
    return ease_out((t - delay) / d)


@lru_cache(maxsize=None)
def font(size, bold=True):
    candidates = (
        ["/System/Library/Fonts/Supplemental/Arial Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"] if bold else
        ["/System/Library/Fonts/Supplemental/Arial.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    )
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


EMOJI_FONTS = [("/System/Library/Fonts/Apple Color Emoji.ttc", 160),
               ("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf", 109)]


@lru_cache(maxsize=None)
def emoji(ch, size):
    """Colour emoji as an RGBA image, size x size. Emoji fonts only come in fixed sizes, so scale."""
    ch = ch.replace("️", "")
    for path, native in EMOJI_FONTS:
        if Path(path).exists():
            f = ImageFont.truetype(path, native)
            img = Image.new("RGBA", (native * 2, native * 2), (0, 0, 0, 0))
            ImageDraw.Draw(img).text((native // 2, native // 2), ch, font=f, embedded_color=True)
            box = img.getbbox()
            if box:
                img = img.crop(box)
                scale = size / max(img.size)
                return img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)
    # no emoji font: a plain disc with the first letter
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([0, 0, size - 1, size - 1], fill=PANEL, outline=CYAN, width=max(2, size // 30))
    d.text((size / 2, size / 2), ch[:1], font=font(size // 2), fill=WHITE, anchor="mm")
    return img


def paste_icon(canvas, ch, size, cx, cy, alpha=1.0, scale=1.0):
    if alpha <= 0.01:
        return
    s = max(4, int(size * scale))
    img = emoji(ch, s)
    if alpha < 1:
        img = img.copy()
        img.putalpha(img.getchannel("A").point(lambda a: int(a * alpha)))
    canvas.alpha_composite(img, (int(cx - img.width / 2), int(cy - img.height / 2)))


def text(d, xy, s, size, fill=WHITE, bold=True, anchor="mm", alpha=1.0, stroke=0):
    if alpha <= 0.01:
        return
    fill = tuple(fill[:3]) + (int(255 * alpha),)
    d.text(xy, s, font=font(size, bold), fill=fill, anchor=anchor,
           stroke_width=stroke, stroke_fill=(0, 0, 0, int(255 * alpha)))




# ---------------------------------------------------------------- beats

def beat_title(c, d, show, t, dur):
    icons = show.get("icons", [])
    n = max(1, len(icons))
    gap = min(300, (SAFE_R - SAFE_L) / n)
    x0 = W / 2 - gap * (n - 1) / 2
    if n > 1:   # a pulse travelling along the row
        d.line([x0, STAGE_CY + 150, x0 + gap * (n - 1), STAGE_CY + 150], fill=GREY + (90,), width=4)
        p = (t * 0.8) % 1
        x = x0 + p * gap * (n - 1)
        d.ellipse([x - 14, STAGE_CY + 136, x + 14, STAGE_CY + 164], fill=YELLOW + (230,))
    for i, ic in enumerate(icons):
        a = appear(t, 0.15 * i)
        bob = math.sin((t + i) * 2.2) * 10
        paste_icon(c, ic, 230, x0 + i * gap, STAGE_CY + bob, a, 0.6 + 0.4 * a)


def _grid(n):
    cols = 6
    rows = math.ceil(n / cols)
    xs = np.linspace(SAFE_L + 70, SAFE_R - 70, cols)
    ys = np.linspace(STAGE_CY - 190, STAGE_CY + 190, rows)
    return [(xs[i % cols], ys[i // cols]) for i in range(n)]


def _pieces(c, d, t, dur, icon, reverse):
    n = 24
    grid = _grid(n)
    p_icon = 1 - ease_in_out(t / 0.5) if not reverse else ease_in_out((t - dur * 0.55) / 0.5)
    paste_icon(c, icon, 360, W / 2, STAGE_CY, p_icon, 0.7 + 0.3 * p_icon)
    for i, (gx, gy) in enumerate(grid):
        if not reverse:
            p = ease_in_out((t - 0.3 - i * 0.025) / 0.7)
        else:
            p = 1 - ease_in_out((t - 0.2 - (n - i) * 0.02) / (dur * 0.45))
        x = W / 2 + (gx - W / 2) * p
        y = STAGE_CY + (gy - STAGE_CY) * p + math.sin(t * 3 + i) * 6 * p
        s = 30 + 16 * p
        if 0.02 < p:
            col = PALETTE[i % len(PALETTE)]
            d.rounded_rectangle([x - s / 2, y - s / 2, x + s / 2, y + s / 2], radius=8, fill=col + (230,))


def beat_split(c, d, show, t, dur):
    _pieces(c, d, t, dur, show.get("icon", "🖼"), reverse=False)


def beat_merge(c, d, show, t, dur):
    _pieces(c, d, t, dur, show.get("icon", "🖼"), reverse=True)


def beat_focus(c, d, show, t, dur):
    a = appear(t)
    for k in range(2):                       # two expanding rings
        ring = (t * 0.7 + k / 2) % 1
        r = 190 + ring * 170
        d.ellipse([W / 2 - r, STAGE_CY - r, W / 2 + r, STAGE_CY + r],
                  outline=CYAN + (int(170 * (1 - ring) * a),), width=5)
    scale = (0.7 + 0.3 * a) * (1 + 0.03 * math.sin(t * 3))
    paste_icon(c, show.get("icon", "💡"), 340, W / 2, STAGE_CY, a, scale)


def beat_flow(c, d, show, t, dur):
    nodes = show.get("nodes", [])[:4]
    n = max(2, len(nodes))
    xs = np.linspace(SAFE_L + 95, SAFE_R - 95, n)
    y = STAGE_CY
    color = YELLOW if show.get("style") == "light" else CYAN
    for k in range(int(xs[0]), int(xs[-1]), 28):       # dashed track
        d.line([k, y, k + 14, y], fill=GREY + (110,), width=4)
    for k in range(7):                                   # packets flowing left to right
        if t < 0.4:
            break
        p = ((t * 0.55 + k / 7) % 1)
        x = xs[0] + (xs[-1] - xs[0]) * p
        if show.get("style") == "light":
            d.line([x - 70, y, x, y], fill=color + (120,), width=12)
        d.ellipse([x - 14, y - 14, x + 14, y + 14], fill=color + (240,))
    for i, node in enumerate(nodes):
        a = appear(t, 0.12 * i)
        d.ellipse([xs[i] - 110, y - 110, xs[i] + 110, y + 110], fill=PANEL + (int(235 * a),),
                  outline=color + (int(200 * a),), width=5)
        paste_icon(c, node[0], 130, xs[i], y, a, 0.7 + 0.3 * a)


def _number(value, p):
    m = re.match(r"^([^\d]*)([\d.,]+)(.*)$", str(value))
    if not m:
        return str(value)
    pre, num, post = m.groups()
    digits = num.replace(",", "")
    try:
        target = float(digits)
    except ValueError:
        return str(value)
    decimals = len(digits.split(".")[1]) if "." in digits else 0
    cur = target * ease_out(p)
    s = f"{cur:,.{decimals}f}" if "," in num else f"{cur:.{decimals}f}"
    return pre + s + post


def beat_stat(c, d, show, t, dur):
    p = clamp(t / max(0.6, dur * 0.6))
    if show.get("icon"):
        paste_icon(c, show["icon"], 200, W / 2, STAGE_CY - 200, appear(t), 1 + 0.04 * math.sin(t * 3))
    value = _number(show.get("value", ""), p)
    size = 200 if len(value) <= 7 else int(200 * 7 / len(value)) + 20
    text(d, (W / 2, STAGE_CY + 60), value, size, YELLOW, alpha=appear(t))


def beat_list(c, d, show, t, dur):
    items = show.get("items", [])[:4]
    n = max(1, len(items))
    step = max(0.45, (dur - 0.5) / n)
    xs = np.linspace(SAFE_L + 100, SAFE_R - 100, n) if n > 1 else [W / 2]
    for i, item in enumerate(items):
        a = appear(t, 0.2 + i * step)
        d.ellipse([xs[i] - 90, STAGE_CY - 90, xs[i] + 90, STAGE_CY + 90], fill=PANEL + (int(220 * a),),
                  outline=PALETTE[i % len(PALETTE)] + (int(220 * a),), width=5)
        paste_icon(c, item[0], 110, xs[i], STAGE_CY - 30 * (1 - a), a)


def beat_compare(c, d, show, t, dur):
    for side, x, delay in (("left", 290, 0.0), ("right", 790, 0.35)):
        item = show.get(side, ["❓"])
        a = appear(t, delay)
        paste_icon(c, item[0], 260, x, STAGE_CY + math.sin(t * 2 + x) * 8, a, 0.7 + 0.3 * a)
    a = appear(t, 0.2)
    d.ellipse([W / 2 - 55, STAGE_CY - 55, W / 2 + 55, STAGE_CY + 55], fill=YELLOW + (int(255 * a),))
    text(d, (W / 2, STAGE_CY), "vs", 48, (12, 14, 18), alpha=a)


BEATS = dict(title=beat_title, split=beat_split, merge=beat_merge, focus=beat_focus,
             flow=beat_flow, stat=beat_stat, list=beat_list, compare=beat_compare)


# ---------------------------------------------------------------- frame layers

def make_background():
    """Navy gradient with a faint dot grid; slightly larger than the frame so it can drift."""
    bg = Image.new("RGB", (W + 80, H + 80))
    top, bottom = np.array((10, 14, 30)), np.array((20, 30, 58))
    grad = np.linspace(0, 1, H + 80)[:, None, None] * (bottom - top) + top
    bg = Image.fromarray(np.repeat(grad, W + 80, axis=1).astype(np.uint8))
    d = ImageDraw.Draw(bg)
    for y in range(0, H + 80, 54):
        for x in range(0, W + 80, 54):
            d.ellipse([x - 2, y - 2, x + 2, y + 2], fill=(40, 52, 86))
    return bg


@lru_cache(maxsize=None)
def watermark():
    f = font(32)
    logo = 64
    wm = Image.new("RGBA", (logo + 12 + int(f.getlength(HANDLE)) + 8, logo + 4), (0, 0, 0, 0))
    if LOGO_MARK.exists():
        wm.alpha_composite(Image.open(LOGO_MARK).convert("RGBA").resize((logo, logo), Image.LANCZOS), (0, 2))
    ImageDraw.Draw(wm).text((logo + 10, wm.height // 2), HANDLE, font=f, fill=(225, 230, 240),
                            anchor="lm")
    return wm


def subtitle_lines(words):
    """One word on screen at a time (kept for the render loop's per-beat lookup)."""
    return words


def draw_subtitle(d, words, now):
    word = next((w for w in words if w[1] - 0.04 <= now <= w[2] + 0.12), None)
    if not word:
        return
    w, s, e = word
    pop = 1 + 0.12 * (1 - ease_out((now - s) / 0.12))          # small pop as each word appears
    d.text((W / 2, SUB_Y), w.strip(".,!?;:"), font=font(int(92 * pop)), fill=WHITE + (255,),
           anchor="mm", stroke_width=7, stroke_fill=(0, 0, 0, 255))


def draw_outro(c, d, t, line=None):
    a = appear(t)
    if LOGO_MARK.exists():
        logo = Image.open(LOGO_MARK).convert("RGBA").resize((340, 340), Image.LANCZOS)
        logo.putalpha(logo.getchannel("A").point(lambda v: int(v * a)))
        c.alpha_composite(logo, (W // 2 - 170, STAGE_CY - 220))
    text(d, (W / 2, STAGE_CY + 190), HANDLE, 54, YELLOW, alpha=appear(t, 0.2))


# ---------------------------------------------------------------- audio

def to_44k(audio):
    n = int(len(audio) * SR / tts.SR)
    return np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)


def write_wav(path, stereo):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(stereo, -1, 1) * 32767).astype(np.int16).tobytes())


# ---------------------------------------------------------------- render

OUTRO_LINE = "Follow for more of how the world works."
GAP = 0.25          # pause between beats


def render(topic, out_dir, log=print):
    """Render one topic. Returns dict(video, duration)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. narration per beat (+ outro), with word timings on one timeline
    segments, t0 = [], 0.4
    voice = []
    for beat in topic["beats"] + [{"say": OUTRO_LINE, "show": {"type": "outro"}}]:
        audio, words = tts.speak(beat["say"])
        dur = len(audio) / tts.SR
        segments.append(dict(beat=beat, start=t0, dur=dur + GAP,
                             words=[(w, t0 + s, t0 + e) for w, s, e in words]))
        voice.append((t0, audio))
        t0 += dur + GAP
    total = t0 + 0.6
    log(f"  narration {total:.1f}s, {len(segments)} beats")

    # 2. audio: voice on top of a quiet music bed
    n = int(SR * total)
    mix = np.zeros((n, 2), np.float32)
    bed = music.generate(topic.get("mood", "tech"), total, seed=sum(map(ord, topic["id"])))
    mix += bed[:n] * 0.16
    for start, audio in voice:
        a = to_44k(audio)
        i = int(start * SR)
        mix[i:i + len(a)] += a[: n - i, None] * 0.95
    mix = mix / max(1.0, float(np.abs(mix).max()) / 0.95)

    # 3. video
    bg = make_background()
    lines = [subtitle_lines(s["words"]) for s in segments]
    video = out_dir / "reel.mp4"
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "audio.wav"
        write_wav(wav, mix)
        writer = imageio_ffmpeg.write_frames(
            str(video), (W, H), fps=FPS, codec="libx264", quality=None, macro_block_size=1,
            audio_path=str(wav), audio_codec="aac",
            output_params=["-crf", "20", "-preset", "medium", "-movflags", "+faststart",
                           "-af", "loudnorm=I=-15:TP=-1.5:LRA=11",   # as loud as typical Reels
                           "-ar", "44100", "-shortest", "-b:a", "160k"])
        writer.send(None)
        cover = None
        for f in range(int(total * FPS)):
            now = f / FPS
            seg_i = max(0, next((i for i, s in reversed(list(enumerate(segments))) if now >= s["start"]), 0))
            seg = segments[seg_i]
            t = now - seg["start"]
            drift = int((now * 12) % 54)
            frame = bg.crop((drift, drift, drift + W, drift + H)).convert("RGBA")
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d = ImageDraw.Draw(layer)
            show = seg["beat"]["show"]
            if show["type"] == "outro":
                draw_outro(layer, d, t)
            else:
                BEATS[show["type"]](layer, d, show, t, seg["dur"])
            # quick fade between beats
            fade = min(appear(t, 0, 0.25), 1.0)
            if fade < 1:
                layer.putalpha(layer.getchannel("A").point(lambda v: int(v * fade)))
            frame.alpha_composite(layer)
            d2 = ImageDraw.Draw(frame)
            draw_subtitle(d2, lines[seg_i], now)
            frame.alpha_composite(watermark(), (SAFE_L, 190))
            rgb = frame.convert("RGB")
            if seg_i == 1 and cover is None and t > 1.0:
                cover = rgb
            writer.send(np.asarray(rgb).tobytes())
        writer.close()
    (cover or rgb).save(out_dir / "cover.jpg", quality=92)
    return dict(video=str(video), duration=total)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--topic", required=True, help="id from explainers.json")
    p.add_argument("--out", default="out")
    args = p.parse_args()
    topics = {x["id"]: x for x in json.loads((ROOT / "explainers.json").read_text())}
    if args.topic not in topics:
        sys.exit(f"Unknown topic. Choose from: {', '.join(topics)}")
    result = render(topics[args.topic], Path(args.out) / args.topic)
    print(f"Video: {result['video']} ({result['duration']:.1f}s)")


if __name__ == "__main__":
    main()
