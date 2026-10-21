#!/usr/bin/env python3
"""Build a vertical (9:16) satellite timelapse Reel of a place, oldest year first.

Imagery comes from Microsoft Planetary Computer (free, no account needed):
  - landsat:  Landsat 5/7/8/9 Collection 2 Level-2, 30 m, 1984 -> now
  - sentinel: Sentinel-2 L2A, 10 m, 2016 -> now

For each year, the clearest scenes in the chosen season are cloud-masked and
median-composited onto one fixed grid, so every frame lines up.

Examples:
  python timelapse.py --place tuz-golu
  python timelapse.py --name "Lake Mead" --lat 36.15 --lon -114.45 --size-km 40 --start 1985
"""

import argparse
import datetime as dt
import json
import math
import sys
import tempfile
import textwrap
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import imageio_ffmpeg
import numpy as np
import planetary_computer
import pystac_client
import rasterio
from PIL import Image, ImageDraw, ImageFont
from pyproj import CRS, Transformer
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.vrt import WarpedVRT

import music

ROOT = Path(__file__).parent
STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
W, H = 1080, 1920          # Reels size
MAP = 1080                 # square map, full width
MAP_TOP = 470              # leaves room for the hook and timeline above; Reels UI covers the bottom ~350 px
SAFE_L, SAFE_R = 120, W - 120   # tall phones crop ~100 px off each side of a 9:16 Reel
FPS = 30
HANDLE = "@gecmistenharita"
LOGO_MARK = ROOT / "assets" / "logo_mark.png"

GDAL_ENV = dict(
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    GDAL_HTTP_MULTIRANGE="YES",
    GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES",
    GDAL_HTTP_MAX_RETRY="4",
    GDAL_HTTP_RETRY_DELAY="1",
    VSI_CACHE="TRUE",
)

SOURCES = {
    "landsat": dict(
        collection="landsat-c2-l2",
        rgb=["red", "green", "blue"],
        qa="qa_pixel",
        credit="Görüntü: NASA / USGS Landsat",
        first_year=1984,
    ),
    "sentinel": dict(
        collection="sentinel-2-l2a",
        rgb=["B04", "B03", "B02"],
        qa="SCL",
        credit="Contains modified Copernicus Sentinel data",
        first_year=2016,
    ),
}

DEFAULTS = dict(
    subtitle="", size_km=30, start=1990, end=None, step=None, source="landsat",
    season="05-01:09-30", max_cloud=30, scenes=5, min_coverage=0.85,  # gaps are filled from neighbouring years
    max_refl=0.3, gamma=1.4,
)


# ---------------------------------------------------------------- geometry

def target_grid(lat, lon, size_km):
    """Square UTM grid centred on lat/lon, MAP x MAP pixels."""
    zone = int((lon + 180) // 6) + 1
    epsg = (32600 if lat >= 0 else 32700) + zone
    crs = CRS.from_epsg(epsg)
    to_utm = Transformer.from_crs(4326, crs, always_xy=True)
    to_ll = Transformer.from_crs(crs, 4326, always_xy=True)
    cx, cy = to_utm.transform(lon, lat)
    half = size_km * 500
    bounds = (cx - half, cy - half, cx + half, cy + half)
    transform = from_bounds(*bounds, MAP, MAP)
    corners = [to_ll.transform(x, y) for x in bounds[0::2] for y in bounds[1::2]]
    lons, lats = zip(*corners)
    return crs, transform, [min(lons), min(lats), max(lons), max(lats)]


def last_complete_year(season):
    """Most recent year whose season window has already ended."""
    today = dt.date.today()
    month, day = map(int, season.split(":")[1].split("-"))
    return today.year if today > dt.date(today.year, month, day) else today.year - 1


# ---------------------------------------------------------------- imagery

def search(catalog, source, year, season, bbox_ll, max_cloud, max_items):
    if source == "landsat" and 2003 <= year:
        # Landsat 7 has striped data (SLC failure) after May 2003, so prefer the
        # other satellites. Where they have no coverage, stack extra Landsat 7
        # scenes: their stripes fall in different places, so the median fills in.
        items = _search(catalog, source, year, season, bbox_ll, max_cloud, max_items,
                        ["landsat-5", "landsat-8", "landsat-9"])
        return items or _search(catalog, source, year, season, bbox_ll, max_cloud,
                                max_items * 2, ["landsat-7"])
    return _search(catalog, source, year, season, bbox_ll, max_cloud, max_items)


def _search(catalog, source, year, season, bbox_ll, max_cloud, max_items, platforms=None):
    s, e = season
    query = {"eo:cloud_cover": {"lt": max_cloud}}
    if platforms:
        query["platform"] = {"in": platforms}
    items = catalog.search(
        collections=[SOURCES[source]["collection"]],
        bbox=bbox_ll,
        datetime=f"{year}-{s}/{year}-{e}",
        query=query,
        max_items=200,
    ).item_collection()
    items = sorted(items, key=lambda it: it.properties.get("eo:cloud_cover", 100))
    return list(items)[:max_items]


def read_band(href, crs, transform, resampling):
    with rasterio.Env(**GDAL_ENV):
        with rasterio.open(href) as src:
            # Read from the coarsest built-in overview that is still sharper than the
            # output, instead of pulling full-resolution pixels over the network.
            factor = transform.a / src.res[0]
            level = max((i for i, o in enumerate(src.overviews(1)) if o <= factor), default=None)
        with rasterio.open(href, overview_level=level) as src, WarpedVRT(src, crs=crs, transform=transform, width=MAP, height=MAP,
                resampling=resampling, src_nodata=src.nodata, nodata=0) as vrt:
            return vrt.read(1)


def read_item(item, source, crs, transform):
    """Return (H, W, 3) float32 reflectance with NaN where invalid."""
    cfg = SOURCES[source]
    rgb = [read_band(item.assets[b].href, crs, transform, Resampling.bilinear)
           for b in cfg["rgb"]]
    qa = read_band(item.assets[cfg["qa"]].href, crs, transform, Resampling.nearest)
    rgb = np.stack(rgb, axis=-1).astype(np.float32)
    valid = (rgb > 0).all(axis=-1)

    if source == "landsat":
        refl = rgb * 0.0000275 - 0.2
        q = qa.astype(np.uint16)
        bad = q & ((1 << 0) | (1 << 3) | (1 << 4))     # fill, cloud, cloud shadow
        valid &= bad == 0
    else:
        offset = 1000 if float(item.properties.get("s2:processing_baseline", "0")) >= 4.0 else 0
        refl = (rgb - offset) / 10000.0
        valid &= ~np.isin(qa, [0, 1, 8, 9, 10])   # nodata, defect, clouds, cirrus (shadow class often flags water)

    refl[~valid] = np.nan
    return refl


def composite(items, source, crs, transform):
    with ThreadPoolExecutor(max_workers=6) as pool:
        stacks = list(pool.map(lambda it: read_item(it, source, crs, transform), items))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN pixels
        comp = np.nanmedian(np.stack(stacks), axis=0)
    coverage = float(np.isfinite(comp).all(axis=-1).mean())
    return comp, coverage


def fill_holes(maps, holes):
    """Patch small gaps (cloud holes, Landsat 7 stripes) from the nearest year that has the pixel."""
    for order in (range(1, len(maps)), range(len(maps) - 2, -1, -1)):   # forward, then backward
        for i in order:
            j = i - 1 if order.step == 1 else i + 1
            take = holes[i] & ~holes[j]
            maps[i][take] = maps[j][take]
            holes[i] &= ~take


def to_rgb8(refl, max_refl, gamma):
    img = np.clip(np.nan_to_num(refl, nan=0) / max_refl, 0, 1) ** (1 / gamma)
    return (img * 255).astype(np.uint8)


# ---------------------------------------------------------------- drawing

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


def nice_scale(size_km):
    target = size_km / 5
    exp = 10 ** math.floor(math.log10(target))
    for m in (5, 2, 1):
        if m * exp <= target:
            return m * exp
    return exp


_watermark = None


def watermark():
    """Logo + handle, drawn once and reused on every frame."""
    global _watermark
    if _watermark is None:
        f = font(34)
        text_w = int(f.getlength(HANDLE))
        logo = 76
        wm = Image.new("RGBA", (logo + 14 + text_w + 12, logo + 8), (0, 0, 0, 0))
        if LOGO_MARK.exists():
            mark = Image.open(LOGO_MARK).convert("RGBA").resize((logo, logo), Image.LANCZOS)
            wm.alpha_composite(mark, (0, 4))
        ImageDraw.Draw(wm).text((logo + 12, wm.height // 2), HANDLE, font=f, fill="white",
                                stroke_width=3, stroke_fill=(0, 0, 0), anchor="lm")
        _watermark = wm
    return _watermark


def background(title, hook, credit):
    """Everything that stays the same in every frame (except the map)."""
    canvas = Image.new("RGB", (W, H), (12, 14, 18))
    d = ImageDraw.Draw(canvas)
    lines = textwrap.wrap(hook, width=28)[:3]
    d.text((W // 2, 170 if len(lines) < 3 else 150), title, font=font(58), fill="white", anchor="mm")
    f = font(52)
    y = 240 if len(lines) < 3 else 222
    for line in lines:
        d.text((W // 2, y), line, font=f, fill=(255, 214, 92), anchor="mm")
        y += 62
    return canvas


def compose(bg, map_img, year, progress, years, size_km, credit, end_card=False):
    """One video frame. Everything important stays inside SAFE_L..SAFE_R, because tall phones crop
    the sides, and away from the like/comment/share buttons on the lower right."""
    canvas = bg.copy()
    canvas.paste(Image.fromarray(map_img), (0, MAP_TOP))
    d = ImageDraw.Draw(canvas)

    # timeline between the hook and the map: oldest -> newest
    ty, tx0, tx1 = MAP_TOP - 45, SAFE_L + 95, SAFE_R - 95
    d.line([tx0, ty, tx1, ty], fill=(70, 76, 88), width=6)
    xp = tx0 + (tx1 - tx0) * progress
    d.line([tx0, ty, xp, ty], fill=(255, 214, 92), width=6)
    d.ellipse([xp - 13, ty - 13, xp + 13, ty + 13], fill=(255, 214, 92))
    small = font(30)
    d.text((tx0 - 16, ty), str(years[0]), font=small, fill=(170, 178, 190), anchor="rm")
    d.text((tx1 + 16, ty), str(years[-1]), font=small, fill=(170, 178, 190), anchor="lm")

    # year, big, top-left of the map
    d.text((SAFE_L, MAP_TOP + 30), str(year), font=font(130), fill="white",
           stroke_width=6, stroke_fill=(0, 0, 0), anchor="la")

    # watermark, top-right of the map (the lower right is under Instagram's buttons)
    wm = watermark()
    canvas.paste(wm, (SAFE_R - wm.width, MAP_TOP + 40), wm)

    # follow card while the last (newest) year is held
    if end_card:
        f = font(46)
        label = f"Takip et → {HANDLE}"
        tw = f.getlength(label)
        cy = MAP_TOP + 260
        d.rounded_rectangle([W / 2 - tw / 2 - 34, cy - 44, W / 2 + tw / 2 + 34, cy + 44],
                            radius=44, fill=(255, 214, 92))
        d.text((W / 2, cy), label, font=f, fill=(12, 14, 18), anchor="mm")

    # scale bar, bottom-left of the map
    km = nice_scale(size_km)
    px = km / size_km * MAP
    x0, y0 = SAFE_L, MAP_TOP + MAP - 60
    d.rectangle([x0, y0, x0 + px, y0 + 8], fill="white", outline="black")
    d.text((x0 + px / 2, y0 - 22), f"{km:g} km", font=font(30), fill="white",
           stroke_width=3, stroke_fill="black", anchor="mm")

    # imagery credit along the bottom edge of the map
    d.text((500, MAP_TOP + MAP - 22), credit, font=font(22, bold=False), fill=(225, 228, 235),
           stroke_width=2, stroke_fill=(0, 0, 0), anchor="mm")
    return np.asarray(canvas)


# ---------------------------------------------------------------- audio + video

def write_video(path, maps, years, bg, size_km, credit, seed, mood="calm"):
    n = len(maps)
    hold = max(9, min(24, int(18 * FPS / n)))       # aim for ~20-30 s total
    fade = max(5, hold // 2)
    end_hold = int(2.5 * FPS)
    total = sum(end_hold if i == n - 1 else hold + fade for i in range(n))

    with tempfile.TemporaryDirectory() as tmp:
        seconds = total / FPS
        audio = music.soundtrack(mood, seconds + 0.5, seed, tmp)
        writer = imageio_ffmpeg.write_frames(
            str(path), (W, H), fps=FPS, codec="libx264", quality=None, macro_block_size=1,
            audio_path=str(audio), audio_codec="aac",
            output_params=["-crf", "20", "-preset", "medium", "-movflags", "+faststart",
                           "-af", f"afade=t=in:d=0.5,afade=t=out:st={seconds - 2:.2f}:d=2",
                           "-shortest", "-b:a", "160k"],
        )
        writer.send(None)
        step = 1 / (n - 1)
        for i in range(n):
            a = maps[i]
            last = i == n - 1
            for k in range(end_hold if last else hold):
                card = last and k >= FPS // 2          # show the follow card half a second in
                writer.send(compose(bg, a, years[i], i * step, years, size_km, credit, card).tobytes())
            if i + 1 < n:
                af, bf = a.astype(np.float32), maps[i + 1].astype(np.float32)
                for k in range(1, fade + 1):
                    t = k / (fade + 1)
                    blend = (af * (1 - t) + bf * t).astype(np.uint8)
                    year = years[i + 1] if t >= 0.5 else years[i]
                    writer.send(compose(bg, blend, year, (i + t) * step, years, size_km, credit).tobytes())
        writer.close()
    return total / FPS


# ---------------------------------------------------------------- render

def render(place, out_dir, hook=None, log=print):
    """Render one place. Returns dict with video path, years, duration; None if too few years."""
    cfg = {**DEFAULTS, **place}
    source = cfg["source"]
    season = cfg["season"].split(":")
    start = max(cfg["start"], SOURCES[source]["first_year"])
    end = cfg["end"] or last_complete_year(cfg["season"])
    out_dir = Path(out_dir)
    (out_dir / "frames").mkdir(parents=True, exist_ok=True)

    crs, transform, bbox_ll = target_grid(cfg["lat"], cfg["lon"], cfg["size_km"])
    catalog = pystac_client.Client.open(STAC_URL, modifier=planetary_computer.sign_inplace)

    maps, holes, years = [], [], []
    step = cfg["step"] or (2 if end - start > 25 else 1)   # every 2nd year on long ranges: half the downloads
    for year in range(start, end + 1, step):
        try:
            items = search(catalog, source, year, season, bbox_ll, cfg["max_cloud"], cfg["scenes"])
            if not items:
                log(f"  {year}: no clear scenes, skipped")
                continue
            comp, cov = composite(items, source, crs, transform)
        except Exception as e:  # network hiccups, missing assets
            log(f"  {year}: failed ({e}), skipped")
            continue
        if cov < cfg["min_coverage"]:
            # cloudy year: try again with three times as many scenes and a looser cloud filter
            try:
                more = search(catalog, source, year, season, bbox_ll,
                              min(cfg["max_cloud"] * 2, 80), cfg["scenes"] * 3)
                if len(more) > len(items):
                    items = more
                    comp, cov = composite(items, source, crs, transform)
            except Exception as e:
                log(f"  {year}: retry failed ({e})")
        if cov < cfg["min_coverage"]:
            log(f"  {year}: only {cov:.0%} clear from {len(items)} scenes, skipped")
            continue
        maps.append(to_rgb8(comp, cfg["max_refl"], cfg["gamma"]))
        holes.append(~np.isfinite(comp).all(axis=-1))
        years.append(year)
        log(f"  {year}: ok ({len(items)} scenes, {cov:.0%} clear)")

    if len(maps) < 4 or years[-1] < end - 2:   # need a recent image too
        log("  not enough usable years")
        return None

    fill_holes(maps, holes)
    for year, img in zip(years, maps):
        Image.fromarray(img).save(out_dir / "frames" / f"{year}.jpg", quality=92)

    credit = SOURCES[source]["credit"]
    if source == "sentinel":
        credit += f" {years[0]}–{years[-1]}"
    bg = background(cfg["name"], hook or cfg.get("subtitle", ""), credit)
    video = out_dir / "reel.mp4"
    seed = sum(map(ord, cfg["name"]))
    duration = write_video(video, maps, years, bg, cfg["size_km"], credit, seed, cfg.get("mood", "calm"))
    Image.fromarray(compose(bg, maps[-1], years[-1], 1.0, years, cfg["size_km"], credit)).save(
        out_dir / "cover.jpg", quality=92)
    return dict(video=str(video), years=years, duration=duration, source=source)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--place", help="id from places_tr.json")
    p.add_argument("--name")
    p.add_argument("--hook", help="question shown above the map")
    p.add_argument("--lat", type=float)
    p.add_argument("--lon", type=float)
    p.add_argument("--size-km", type=float)
    p.add_argument("--start", type=int)
    p.add_argument("--end", type=int)
    p.add_argument("--step", type=int)
    p.add_argument("--source", choices=SOURCES)
    p.add_argument("--season", help="MM-DD:MM-DD window each year")
    p.add_argument("--max-cloud", type=float)
    p.add_argument("--scenes", type=int)
    p.add_argument("--out", default="out")
    args = p.parse_args()

    place = {}
    if args.place:
        places = {pl["id"]: pl for pl in json.loads((ROOT / "places_tr.json").read_text())}
        if args.place not in places:
            sys.exit(f"Unknown place. Choose from: {', '.join(places)}")
        place = dict(places[args.place])
    for k, v in vars(args).items():
        if v is not None and k not in ("place", "out", "hook"):
            place[k] = v
    if not all(k in place for k in ("name", "lat", "lon")):
        sys.exit("Give --place, or --name, --lat and --lon.")
    hook = args.hook or (place.get("hooks") or [None])[0]

    out = Path(args.out) / (args.place or place["name"].lower().replace(" ", "-"))
    result = render(place, out, hook)
    if not result:
        sys.exit("Not enough usable years. Try a wider season or higher --max-cloud.")
    print(f"\nVideo: {result['video']} ({result['duration']:.1f}s, {len(result['years'])} years)")


if __name__ == "__main__":
    main()
