#!/usr/bin/env python3
"""Pick the next place, render its Reel, and post it to Instagram. Run once per post.

  python autopilot.py render                 # render the next place into work/pending/
  python autopilot.py render --dry-run       # render + print caption, nothing saved for posting
  python autopilot.py publish --video-url U  # post work/pending/ (Instagram downloads the video from U)
  python autopilot.py status                 # what's been posted, what's next
  python autopilot.py check                  # verify the Instagram token works

Instagram's API (Instagram Login) only accepts a public video URL, so the GitHub
workflow puts reel.mp4 on GitHub Pages between `render` and `publish`.

Needs IG_ACCESS_TOKEN (environment, or a local .env file); the account id is looked up from it.
Progress lives in state.json, so each run continues where the last one stopped.

Each place is used twice: round 1 is the close-up view with the first hook,
round 2 is a wider view with the second hook.
"""

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

import captions
import instagram
from timelapse import render

ROOT = Path(__file__).parent
STATE = ROOT / "state.json"
ENV_FILE = ROOT / ".env"
WORK = ROOT / "work"
PENDING = WORK / "pending"
ROUNDS = 2
WIDE_ZOOM = 2.2
MAX_ATTEMPTS = 3          # places to try per run if one has too few clear years
REFRESH_EVERY_DAYS = 7
POSTS_PER_DAY = 24        # matches the hourly schedule in .github/workflows/autopilot.yml


def load_env():
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"posted": [], "failed": [], "token_refreshed_at": None}


def save_state(state):
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def catalog():
    """The world catalog once it exists, otherwise the original Turkey catalog."""
    world = ROOT / "places_world.json"
    return world if world.exists() else ROOT / "places_tr.json"


def queue(state):
    """Places not yet posted or failed, in order: round 1 first, then round 2."""
    places = json.loads(catalog().read_text())
    done = {(p["id"], p["round"]) for p in state["posted"] + state["failed"]}
    for rnd in range(1, ROUNDS + 1):
        for place in places:
            if (place["id"], rnd) in done:
                continue
            place = dict(place)
            hook = place["hooks"][min(rnd - 1, len(place["hooks"]) - 1)]
            if rnd > 1:
                place["size_km"] = min(place["size_km"] * WIDE_ZOOM, 150)
            yield place, rnd, hook


def maybe_refresh_token(state, token):
    last = state.get("token_refreshed_at")
    if last and dt.datetime.fromisoformat(last) > dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=REFRESH_EVERY_DAYS):
        return token
    try:
        new = instagram.refresh_token(token)
    except instagram.InstagramError as e:
        print(f"Token refresh failed (posting with the current token): {e}")
        return token
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::add-mask::{new}")
        if os.environ.get("GH_PAT"):
            subprocess.run(["gh", "secret", "set", "IG_ACCESS_TOKEN", "--body", new,
                            "--repo", os.environ["GITHUB_REPOSITORY"]],
                           check=True, env={**os.environ, "GH_TOKEN": os.environ["GH_PAT"]})
        else:
            print("GH_PAT not set: cannot store the refreshed token. It will expire in 60 days.")
            return new
    elif ENV_FILE.exists():
        lines = [f"IG_ACCESS_TOKEN={new}" if l.startswith("IG_ACCESS_TOKEN=") else l
                 for l in ENV_FILE.read_text().splitlines()]
        ENV_FILE.write_text("\n".join(lines) + "\n")
    state["token_refreshed_at"] = now()
    print("Instagram token refreshed.")
    return new


def render_next(dry_run):
    """Render the next place into work/pending/ (reel.mp4, caption.txt, pending.json)."""
    state = load_state()
    attempts = 0
    for place, rnd, hook in queue(state):
        if attempts >= MAX_ATTEMPTS:
            break
        attempts += 1
        print(f"\n▶ {place['name']} (round {rnd})")
        out = WORK / f"{place['id']}-r{rnd}"
        result = render(place, out, hook)
        if not result:
            state["failed"].append({"id": place["id"], "round": rnd, "at": now(),
                                    "reason": "not enough clear years"})
            save_state(state)
            continue

        caption = captions.build_caption(place, hook, result["years"], result["source"],
                                         index=len(state["posted"]))
        (out / "caption.txt").write_text(caption)
        print(f"  video {result['duration']:.1f}s, {len(result['years'])} years")
        if dry_run:
            print(f"\n--- caption ---\n{caption}\n---------------\nDry run: not posted. Files in {out}")
            return

        PENDING.mkdir(parents=True, exist_ok=True)
        shutil.copy(result["video"], PENDING / "reel.mp4")
        (PENDING / "pending.json").write_text(json.dumps({
            "id": place["id"], "round": rnd, "caption": caption, "duration": result["duration"],
            "years": [result["years"][0], result["years"][-1]],
        }, ensure_ascii=False, indent=2))
        print(f"  ready to post: {PENDING / 'reel.mp4'}")
        return

    if attempts == 0:
        sys.exit(f"The place catalog is used up. Add more places to {catalog().name}.")
    sys.exit(f"No place could be rendered in {attempts} attempts; will try the next ones on the next run.")


def wait_until_online(url, minutes=3):
    """GitHub Pages can take a moment after deploying; Instagram must be able to download the file."""
    deadline = time.time() + minutes * 60
    while time.time() < deadline:
        try:
            if requests.head(url, timeout=20, allow_redirects=True).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(10)
    sys.exit(f"Video never became reachable at {url}")


def publish(video_url):
    """Post work/pending/ as a Reel; Instagram downloads the video from video_url."""
    state = load_state()
    token = os.environ.get("IG_ACCESS_TOKEN")
    if not token:
        sys.exit("IG_ACCESS_TOKEN is required.")
    pending = json.loads((PENDING / "pending.json").read_text())
    try:
        me = instagram.whoami(token)
    except instagram.InstagramError as e:
        sys.exit(f"Instagram token rejected ({describe_token(token)}): {e}")
    print(f"Posting {pending['id']} as @{me.get('username')}")
    token = maybe_refresh_token(state, token)
    save_state(state)

    wait_until_online(video_url)
    media_id, permalink = instagram.publish_reel(
        str(me["user_id"]), token, video_url, pending["caption"],
        thumb_offset_ms=(pending["duration"] - 1.0) * 1000)
    state["posted"].append({"id": pending["id"], "round": pending["round"], "at": now(),
                            "media_id": media_id, "permalink": permalink, "years": pending["years"]})
    save_state(state)
    print(f"✅ posted: {permalink or media_id}")


def status():
    state = load_state()
    upcoming = list(queue(state))
    print(f"Catalog: {catalog().name}   Posted: {len(state['posted'])}   Failed: {len(state['failed'])}"
          f"   Left: {len(upcoming)}  (~{len(upcoming) / POSTS_PER_DAY:.1f} days at {POSTS_PER_DAY}/day)")
    for p in state["posted"][-5:]:
        print(f"  ✔ {p['at'][:16]}  {p['id']} r{p['round']}  {p.get('permalink') or ''}")
    for place, rnd, _ in upcoming[:6]:
        print(f"  … next: {place['id']} r{rnd}")


def clean_secrets():
    """Undo common copy-paste damage: spaces, line breaks, quotes, a pasted 'NAME=' prefix."""
    for name in ("IG_ACCESS_TOKEN", "IG_USER_ID"):
        raw = os.environ.get(name)
        if raw is None:
            continue
        value = "".join(raw.split()).strip("'\"")
        if value.startswith(f"{name}="):
            value = value[len(name) + 1:]
        if value != raw:
            print(f"Note: cleaned extra spaces/quotes from {name}.")
        os.environ[name] = value


def describe_token(token):
    """Shape of the token without revealing it."""
    kind = {"IG": "Instagram token (correct type)", "EA": "Facebook token (wrong type for this API)",
            "gh": "GitHub token (wrong secret pasted)"}.get(token[:2], "unknown type")
    return f"length {len(token)}, starts with '{token[:2]}' → {kind}"


def check():
    token = os.environ.get("IG_ACCESS_TOKEN")
    if not token:
        sys.exit("IG_ACCESS_TOKEN is not set.")
    print(f"IG_ACCESS_TOKEN: {describe_token(token)}")
    try:
        me = instagram.whoami(token)
    except instagram.InstagramError as e:
        sys.exit(f"Token rejected by Instagram: {e}\n"
                 "Generate a new token in the Meta app (Instagram → API setup with Instagram business login → "
                 "Generate token) and store it again in the IG_ACCESS_TOKEN secret.")
    print(f"Token OK: @{me.get('username')} (user_id {me.get('user_id')}, {me.get('account_type')})")


def main():
    load_env()
    clean_secrets()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render", help="render the next place, ready to post")
    r.add_argument("--dry-run", action="store_true", help="render and print the caption only")
    pub = sub.add_parser("publish", help="post the rendered Reel")
    pub.add_argument("--video-url", required=True, help="public URL where Instagram can download reel.mp4")
    sub.add_parser("status")
    sub.add_parser("check")
    args = p.parse_args()
    if args.cmd == "render":
        render_next(args.dry_run)
    elif args.cmd == "publish":
        publish(args.video_url)
    elif args.cmd == "status":
        status()
    else:
        check()


if __name__ == "__main__":
    main()
