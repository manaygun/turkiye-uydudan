#!/usr/bin/env python3
"""Draw the @gecmistenharita logo: a globe (map) with clock hands (time) inside a rewind arrow (the past).

Writes:
  assets/logo.png        1080x1080 profile picture (navy background, safe for Instagram's circle crop)
  assets/logo_mark.png   transparent mark used as the watermark in Reels
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).parent / "assets"
SS = 4                      # supersampling for smooth edges
NAVY = (12, 18, 34)
NAVY_2 = (24, 38, 66)
YELLOW = (255, 214, 92)
SEA = (32, 98, 130)
SEA_2 = (18, 62, 92)
LINE = (140, 205, 222)


def radial_bg(size):
    img = Image.new("RGB", (size, size), NAVY)
    d = ImageDraw.Draw(img)
    steps = 60
    for i in range(steps):
        t = i / steps
        r = size * 0.75 * (1 - t)
        c = tuple(int(NAVY[k] + (NAVY_2[k] - NAVY[k]) * t) for k in range(3))
        d.ellipse([size / 2 - r, size / 2 - r, size / 2 + r, size / 2 + r], fill=c)
    return img


def mark(size):
    """Transparent globe + clock + rewind arrow, drawn in a size x size box."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = size / 2
    rg = size * 0.30                       # globe radius
    ra = size * 0.42                       # arrow radius
    wa = size * 0.06                       # arrow thickness

    # globe: shaded sphere
    globe = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    g = ImageDraw.Draw(globe)
    for i in range(40):
        t = i / 40
        r = rg * (1 - t * 0.85)
        off = rg * 0.25 * t
        col = tuple(int(SEA_2[k] + (SEA[k] - SEA_2[k]) * t) for k in range(3)) + (255,)
        g.ellipse([c - r - off, c - r - off, c + r - off, c + r - off], fill=col)
    # graticule, clipped to the globe
    lines = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    l = ImageDraw.Draw(lines)
    lw = max(1, int(size * 0.008))
    for k in (0.35, 0.7):                  # meridians as ellipses
        rx = rg * k
        l.ellipse([c - rx, c - rg, c + rx, c + rg], outline=LINE + (150,), width=lw)
    l.line([c, c - rg, c, c + rg], fill=LINE + (150,), width=lw)
    for lat in (-0.6, -0.3, 0, 0.3, 0.6):  # parallels
        y = c + rg * lat
        half = rg * math.sqrt(1 - lat * lat)
        l.line([c - half, y, c + half, y], fill=LINE + (150,), width=lw)
    clip = Image.new("L", (size, size), 0)
    ImageDraw.Draw(clip).ellipse([c - rg, c - rg, c + rg, c + rg], fill=255)
    globe.alpha_composite(Image.composite(lines, Image.new("RGBA", (size, size)), clip))
    img.alpha_composite(globe)
    d.ellipse([c - rg, c - rg, c + rg, c + rg], outline=LINE + (255,), width=max(1, int(size * 0.012)))

    # clock hands at about 10:10, pointing "back in time"
    hw = size * 0.032
    for angle, length in ((-150, 0.62), (-55, 0.82)):   # degrees, 0 = 3 o'clock, clockwise
        a = math.radians(angle)
        d.line([c, c, c + math.cos(a) * rg * length, c + math.sin(a) * rg * length],
               fill=YELLOW, width=int(hw))
        tx, ty = c + math.cos(a) * rg * length, c + math.sin(a) * rg * length
        d.ellipse([tx - hw / 2, ty - hw / 2, tx + hw / 2, ty + hw / 2], fill=YELLOW)
    d.ellipse([c - hw, c - hw, c + hw, c + hw], fill=YELLOW)

    # rewind arrow: arc from 250° clockwise round to 320°, gap at the top, tip at 320° heading anticlockwise
    start, end = 250, 320 + 360
    d.arc([c - ra, c - ra, c + ra, c + ra], start=end - 360, end=start, fill=YELLOW, width=int(wa))
    rc = ra - wa / 2                                # PIL draws arc width inward: this is its centre line
    th = math.radians(end - 360)
    px, py = c + rc * math.cos(th), c + rc * math.sin(th)
    fx, fy = math.sin(th), -math.cos(th)            # anticlockwise tangent
    nx, ny = math.cos(th), math.sin(th)             # outward normal
    head_w, head_l = wa * 1.6, wa * 1.9
    d.polygon([
        (px + nx * head_w, py + ny * head_w),
        (px - nx * head_w, py - ny * head_w),
        (px + fx * head_l, py + fy * head_l),
    ], fill=YELLOW)
    # round cap on the tail end
    t2 = math.radians(start)
    qx, qy = c + rc * math.cos(t2), c + rc * math.sin(t2)
    d.ellipse([qx - wa / 2, qy - wa / 2, qx + wa / 2, qy + wa / 2], fill=YELLOW)
    return img


def main():
    OUT.mkdir(exist_ok=True)
    big = 1080 * SS
    logo = radial_bg(big).convert("RGBA")
    m = mark(int(big * 0.82))
    logo.alpha_composite(m, ((big - m.width) // 2, (big - m.height) // 2))
    logo.convert("RGB").resize((1080, 1080), Image.LANCZOS).save(OUT / "logo.png")
    mark(512 * SS).resize((512, 512), Image.LANCZOS).save(OUT / "logo_mark.png")
    print("wrote assets/logo.png and assets/logo_mark.png")


if __name__ == "__main__":
    main()
