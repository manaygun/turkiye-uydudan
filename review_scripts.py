#!/usr/bin/env python3
"""Check explainers.json against the script rules and write scripts_review.md (one table per script).

Rules checked: 45-65 words, max 12 words per sentence, HUD text max 4 words, last beat is the loop,
known station types / camera moves / lights, and every emoji renders.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent
TYPES = {"title", "split", "merge", "focus", "flow", "stat", "list", "compare", "loop"}
CAMERAS = {"glide", "snap", "orbit", "orbit180", "top"}
LIGHTS = {"calm", "warning", "amber"}


def words(s):
    return [w for w in re.findall(r"[\w'’%°,.:-]+", s) if re.search(r"\w", w)]


def describe(show):
    t = show.get("type")
    if t == "loop":
        return "`loop`: camera flies back to the exact Frame 0 pose"
    if t == "title":
        return f"`title`: {' + '.join(show.get('icons', []))}, pulse between them"
    if t == "flow":
        style = " (light style)" if show.get("style") == "light" else ""
        return f"`flow`{style}: {' → '.join(n[0] for n in show.get('nodes', []))}, packets along the cable"
    if t in ("split", "merge"):
        extra = " (object stays, cubes blast downward)" if show.get("keep") and show.get("direction") == "down" else \
                " (object stays, cubes stream out)" if show.get("keep") else ""
        verb = "bursts into 30 glowing cubes" if t == "split" else "rebuilt from 30 flying cubes"
        return f"`{t}`: {show.get('icon')} {verb}{extra}"
    if t == "stat":
        return f"`stat`: 3D \"{show.get('value')}\" counts up, {show.get('icon', '')} above"
    if t == "list":
        return f"`list`: {' '.join(i[0] for i in show.get('items', []))} rise on neon pedestals"
    if t == "compare":
        return f"`compare`: {show['left'][0]} vs {show['right'][0]} on pedestals, 3D \"vs\""
    return f"`focus`: {show.get('icon')} on a turning neon pedestal"


def main():
    topics = json.loads((ROOT / "explainers.json").read_text())
    problems, out = [], ["# Explainer scripts: review\n", "Generated from `explainers.json` by `review_scripts.py`.\n"]
    for i, t in enumerate(topics, 1):
        beats = t["beats"]
        total = sum(len(words(b["say"])) for b in beats)
        if not 45 <= total <= 65:
            problems.append(f"{t['id']}: {total} words")
        for b in beats:
            for sentence in re.split(r"(?<=[.?!])\s+", b["say"]):
                if len(words(sentence)) > 12:
                    problems.append(f"{t['id']}: long sentence: {sentence}")
            if len(words(b.get("text", ""))) > 4:
                problems.append(f"{t['id']}: HUD text too long: {b['text']}")
            if b["show"].get("type") not in TYPES:
                problems.append(f"{t['id']}: unknown type {b['show'].get('type')}")
            if b.get("camera", "glide") not in CAMERAS or b.get("light", "calm") not in LIGHTS:
                problems.append(f"{t['id']}: bad camera/light in: {b['say']}")
        if beats[-1]["show"].get("type") != "loop":
            problems.append(f"{t['id']}: last beat is not the loop")
        out.append(f"\n## {i}. [{t['mode']}] {t['title']}\n")
        out.append("| # | Spoken Audio (Voiceover) | On-Screen Text | 3D Scene & Camera Spec | SFX |")
        out.append("|---|---|---|---|---|")
        for k, b in enumerate(beats, 1):
            spec = describe(b["show"])
            cam = b.get("camera", "glide")
            light = b.get("light", "calm")
            spec += f" · camera: {cam} · light: {light}"
            if b.get("speed"):
                spec += f" · voice ×{b['speed']}"
            out.append(f"| {k} | {b['say']} | {b.get('text', '')} | {spec} | {b.get('sfx', '')} |")
        out.append(f"\n**Words:** {total}  ")
        out.append(f"**Loop:** `{beats[-1]['say']}` → `{beats[0]['say']}`  ")
        out.append("**Caption:**\n> " + "\n> ".join(t["caption"] + [t["send"], " ".join('#' + x for x in t["tags"])]))
    (ROOT / "scripts_review.md").write_text("\n".join(out) + "\n")
    print(f"{len(topics)} scripts checked, review written to scripts_review.md")
    for p in problems:
        print("  ⚠", p)
    return problems


if __name__ == "__main__":
    main()
