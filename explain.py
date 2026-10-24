#!/usr/bin/env python3
"""Make a narrated, looping 3D explainer Reel (1080x1920) from a script, in three stages:

  1. prepare   narration (Kokoro TTS, per-beat speed) + word timings + music bed + SFX -> plan.json, audio.wav
  2. frames    Blender renders the dark-studio 3D scene (scene3d.py), optionally every K-th frame
  3. compose   upscale, HUD headline per beat, one subtitle word at a time, logo, audio -> reel.mp4

The stages are separate so GitHub can render slices of the frames on several machines at once.

Script beat fields:
  say      narration; "—" and "..." make the voice pause
  text     HUD headline shown above the scene for the beat (max ~4 words)
  show     the 3D station: title | split | merge | focus | flow | stat | list | compare,
           or {"type": "loop"} for the last beat (camera flies back to Frame 0)
  camera   glide (default) | snap | orbit | orbit180 | top
  light    calm (cyan, default) | warning (red) | amber
  sfx      e.g. "glitch", "riser+hit", "tick+ding" (see sfx.py)
  speed    voice speed for this beat (default 1.08; 1.15 for quick transitions)

  python explain.py --topic airplane-lift             # all three stages locally
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import wave
from functools import lru_cache
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).parent
W, H = 1080, 1920
FPS = 24
RENDER = dict(width=540, height=960, samples=6)   # upscaled 2x; looks the same for this style
SAFE_L, SAFE_R = 120, W - 120                     # tall phones crop the sides
HUD_Y = 400                                       # beat headline, above the 3D scene
SUB_Y = 1400                                      # subtitle word: below the scene, above Instagram's caption
HANDLE = "@gecmistenharita"
LOGO_MARK = ROOT / "assets" / "logo_mark.png"
SR = 44100
GAP = 0.12                                        # pause between beats (pacing is in the script)
LOOP_TAIL = 0.25                                  # the camera lands on Frame 0 just after the last word

WHITE = (245, 248, 252)
MOOD_RGB = {"calm": (40, 200, 255), "warning": (255, 60, 50), "amber": (255, 160, 30)}


# ---------------------------------------------------------------- helpers

def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def ease_out(x):
    x = clamp(x)
    return 1 - (1 - x) ** 3


@lru_cache(maxsize=None)
def font(size, bold=True):
    candidates = (
        ["/System/Library/Fonts/Supplemental/Arial Black.ttf",
         "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
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


def emoji(ch, size):
    """Colour emoji as an RGBA image (for the 3D badges). Emoji fonts come in fixed sizes, so scale."""
    ch = ch.replace("️", "")
    for path, native in EMOJI_FONTS:
        if Path(path).exists():
            f = ImageFont.truetype(path, native)
            img = Image.new("RGBA", (native * 2, native * 2), (0, 0, 0, 0))
            ImageDraw.Draw(img).text((native // 2, native // 2), ch, font=f, embedded_color=True)
            box = img.getbbox()
            if box:
                img = img.crop(box)
                canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
                scale = size * 0.8 / max(img.size)
                img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)
                canvas.alpha_composite(img, ((size - img.width) // 2, (size - img.height) // 2))
                return canvas
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((size / 2, size / 2), ch[:1], font=font(size // 2), fill=(200, 220, 255), anchor="mm")
    return img


def icons_in(obj, found):
    """Collect the emoji used in a script's visuals."""
    if isinstance(obj, dict):
        for v in obj.values():
            icons_in(v, found)
    elif isinstance(obj, list):
        for v in obj:
            icons_in(v, found)
    elif isinstance(obj, str) and obj and len(obj) <= 3 and not re.search(r"[A-Za-z0-9]", obj):
        found.add(obj.replace("️", ""))
    return found


# ---------------------------------------------------------------- 1. prepare

def to_44k(audio, rate):
    n = int(len(audio) * SR / rate)
    return np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)


def write_wav(path, stereo):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(stereo, -1, 1) * 32767).astype(np.int16).tobytes())


def prepare(topic, out_dir, log=print):
    """Narration, timings, sound and the 3D plan. Writes plan.json, audio.wav, words.json, icons/."""
    import music
    import sfx
    import tts

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    beats, words, voice, t0 = [], [], [], 0.0      # beat 1 starts at Frame 0, so the loop frame-matches
    for k, beat in enumerate(topic["beats"]):
        audio, timed = tts.speak(beat["say"], speed=beat.get("speed") or tts.SPEED)
        dur = len(audio) / tts.SR + GAP
        if beat["show"].get("type") == "loop":
            dur += LOOP_TAIL
        beats.append({"start": round(t0, 3), "dur": round(dur, 3), "show": beat["show"],
                      "text": beat.get("text", ""), "camera": beat.get("camera", "glide"),
                      "light": beat.get("light", "calm"), "sfx": beat.get("sfx", "")})
        words += [[w, round(t0 + s, 3), round(t0 + e, 3)] for w, s, e in timed]
        voice.append((t0, to_44k(audio, tts.SR)))
        t0 += dur
    total = round(t0, 3)
    log(f"  narration {total:.1f}s, {len(beats)} beats")

    n = int(SR * total)
    mix = np.zeros((n, 2), np.float32)
    seed = sum(map(ord, topic["id"]))
    mix += music.generate(topic.get("mood", "tech"), total + 1, seed=seed)[:n] * 0.10
    for k, b in enumerate(beats):                   # one sound effect on every beat
        if b["sfx"]:
            fx = sfx.sfx(b["sfx"], seed=seed + k)
            i = int(b["start"] * SR)
            mix[i:i + len(fx)] += fx[: max(0, n - i), None] * 0.32
    for start, a in voice:
        i = int(start * SR)
        mix[i:i + len(a)] += a[: n - i, None] * 0.95
    write_wav(out_dir / "audio.wav", mix / max(1.0, float(np.abs(mix).max()) / 0.95))

    (out_dir / "icons").mkdir(exist_ok=True)
    icons = {}
    for k, ch in enumerate(sorted(icons_in([b["show"] for b in beats], set()))):
        emoji(ch, 512).save(out_dir / "icons" / f"{k}.png")
        icons[ch] = f"icons/{k}.png"
    plan = dict(fps=FPS, total=total, beats=beats, icons=icons, **RENDER)
    (out_dir / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1))
    (out_dir / "words.json").write_text(json.dumps(words, ensure_ascii=False))
    return plan


# ---------------------------------------------------------------- 2. frames

def blender_binary():
    for cand in (os.environ.get("BLENDER"), shutil.which("blender"),
                 "/Applications/Blender.app/Contents/MacOS/Blender"):
        if cand and Path(cand).exists():
            return cand
    sys.exit("Blender not found: install it or set BLENDER=/path/to/blender")


def render_frames(out_dir, chunk=None, log=print):
    """Render the scene with Blender. chunk=(k, K) renders every K-th frame starting at k, so
    the K machines get an even share of easy and busy parts of the Reel."""
    out_dir = Path(out_dir)
    plan = json.loads((out_dir / "plan.json").read_text())
    n = int(plan["total"] * plan["fps"])
    first, step = (chunk[0], chunk[1]) if chunk else (0, 1)
    log(f"  rendering frames {first}, {first + step}, ... of {n}")
    cmd = [blender_binary(), "-b", "--factory-startup", "-P", str(ROOT / "scene3d.py"), "--",
           str(out_dir / "plan.json"), str(out_dir / "frames"), str(first), str(n), str(step)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or "Traceback" in proc.stdout + proc.stderr:
        sys.exit(f"Blender failed:\n{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}")


# ---------------------------------------------------------------- 3. compose

@lru_cache(maxsize=None)
def watermark():
    f = font(30)
    logo = 60
    wm = Image.new("RGBA", (logo + 12 + int(f.getlength(HANDLE)) + 8, logo + 4), (0, 0, 0, 0))
    if LOGO_MARK.exists():
        wm.alpha_composite(Image.open(LOGO_MARK).convert("RGBA").resize((logo, logo), Image.LANCZOS), (0, 2))
    ImageDraw.Draw(wm).text((logo + 10, wm.height // 2), HANDLE, font=f, fill=(255, 255, 255),
                            anchor="lm", stroke_width=3, stroke_fill=(0, 0, 0))
    return wm


@lru_cache(maxsize=None)
def headline(text, light):
    """Bold HUD headline with a glow in the beat's mood colour (cached per beat)."""
    glow = MOOD_RGB.get(light, MOOD_RGB["calm"])
    size = 104
    f = font(size)
    while f.getlength(text) > SAFE_R - SAFE_L - 20 and size > 56:
        size -= 6
        f = font(size)
    w = int(f.getlength(text)) + 80
    h = size + 80
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    halo = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(halo).text((w / 2, h / 2), text, font=f, fill=glow + (255,), anchor="mm",
                              stroke_width=10, stroke_fill=glow + (255,))
    img.alpha_composite(halo.filter(ImageFilter.GaussianBlur(14)))
    ImageDraw.Draw(img).text((w / 2, h / 2), text, font=f, fill=WHITE + (255,), anchor="mm",
                             stroke_width=4, stroke_fill=(10, 12, 18, 255))
    bar_w = min(w - 80, 220)
    ImageDraw.Draw(img).rounded_rectangle([w / 2 - bar_w / 2, h - 14, w / 2 + bar_w / 2, h - 6], radius=4,
                                          fill=glow + (255,))
    return img


def draw_headline(frame, beat, now):
    if not beat.get("text"):
        return
    img = headline(beat["text"], beat.get("light", "calm"))
    t = now - beat["start"]
    k = ease_out(t / 0.22)
    scale = 0.8 + 0.2 * k
    if scale < 0.99:
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)
    if k < 1:
        img = img.copy()
        img.putalpha(img.getchannel("A").point(lambda v: int(v * k)))
    frame.alpha_composite(img, (int(W / 2 - img.width / 2), int(HUD_Y - img.height / 2)))


def draw_subtitle(d, words, now):
    word = next((w for w in words if w[1] - 0.04 <= now <= w[2] + 0.1), None)
    if not word:
        return
    w, s, _ = word
    w = w.strip(".,!?;:\"“”—…")
    if not w or w in ("—", "..."):
        return
    pop = 1 + 0.12 * (1 - ease_out((now - s) / 0.12))          # small pop as each word appears
    d.text((W / 2, SUB_Y), w, font=font(int(92 * pop)), fill=WHITE + (255,),
           anchor="mm", stroke_width=8, stroke_fill=(0, 0, 0, 255))


def compose(out_dir, log=print):
    """Frames + headlines + subtitles + watermark + audio -> reel.mp4 and cover.jpg."""
    out_dir = Path(out_dir)
    plan = json.loads((out_dir / "plan.json").read_text())
    words = json.loads((out_dir / "words.json").read_text())
    fps, total, beats = plan["fps"], plan["total"], plan["beats"]
    frames = sorted((out_dir / "frames").glob("*.jpg"))
    if not frames:
        sys.exit("No frames rendered")
    by_index = {int(p.stem): p for p in frames}
    video = out_dir / "reel.mp4"
    writer = imageio_ffmpeg.write_frames(
        str(video), (W, H), fps=fps, codec="libx264", quality=None, macro_block_size=1,
        audio_path=str(out_dir / "audio.wav"), audio_codec="aac",
        output_params=["-crf", "19", "-preset", "medium", "-movflags", "+faststart",
                       "-af", "loudnorm=I=-15:TP=-1.5:LRA=11",   # as loud as typical Reels
                       "-ar", "44100", "-shortest", "-b:a", "160k"])
    writer.send(None)
    last_path, cover = frames[0], None
    for f in range(int(total * fps)):
        now = f / fps
        beat = next((b for b in reversed(beats) if now >= b["start"]), beats[0])
        last_path = by_index.get(f, last_path)            # a missing frame repeats the previous one
        frame = Image.open(last_path).convert("RGB").resize((W, H), Image.LANCZOS).convert("RGBA")
        frame.alpha_composite(watermark(), (SAFE_L, 190))
        draw_headline(frame, beat, now)
        draw_subtitle(ImageDraw.Draw(frame), words, now)
        rgb = frame.convert("RGB")
        if cover is None and now >= 1.0:
            cover = rgb
        writer.send(np.asarray(rgb).tobytes())
    writer.close()
    (cover or rgb).save(out_dir / "cover.jpg", quality=92)
    log(f"  composed {video} ({total:.1f}s)")
    return dict(video=str(video), duration=total)


def render(topic, out_dir, log=print):
    """All three stages on one machine. Returns dict(video, duration)."""
    prepare(topic, out_dir, log)
    render_frames(out_dir, log=log)
    return compose(out_dir, log)


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
