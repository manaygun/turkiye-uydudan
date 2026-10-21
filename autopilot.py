#!/usr/bin/env python3
"""Pick the next place, render its Reel, and post it to Instagram. Run once per post.

  python autopilot.py run            # render + post the next place
  python autopilot.py run --dry-run  # render + write caption, don't post
  python autopilot.py status         # what's been posted, what's next
  python autopilot.py check          # verify the Instagram token works

Needs IG_USER_ID and IG_ACCESS_TOKEN (environment, or a local .env file).
Progress lives in state.json, so each run continues where the last one stopped.

Each place is used twice: round 1 is the close-up view with the first hook,
round 2 is a wider view with the second hook.
"""

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

import captions
import instagram
from timelapse import render

ROOT = Path(__file__).parent
STATE = ROOT / "state.json"
ENV_FILE = ROOT / ".env"
WORK = ROOT / "work"
ROUNDS = 2
WIDE_ZOOM = 2.2
MAX_ATTEMPTS = 3          # places to try per run if one has too few clear years
REFRESH_EVERY_DAYS = 7


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


def queue(state):
    """Places not yet posted or failed, in order: round 1 first, then round 2."""
    places = json.loads((ROOT / "places_tr.json").read_text())
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


def run(dry_run):
    state = load_state()
    token, user_id = os.environ.get("IG_ACCESS_TOKEN"), os.environ.get("IG_USER_ID")
    if not dry_run:
        if not token or not user_id:
            sys.exit("IG_ACCESS_TOKEN and IG_USER_ID are required (or use --dry-run).")
        token = maybe_refresh_token(state, token)
        save_state(state)

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

        media_id, permalink = instagram.publish_reel(
            user_id, token, result["video"], caption,
            thumb_offset_ms=(result["duration"] - 1.0) * 1000)
        state["posted"].append({"id": place["id"], "round": rnd, "at": now(),
                                "media_id": media_id, "permalink": permalink,
                                "years": [result["years"][0], result["years"][-1]]})
        save_state(state)
        print(f"  ✅ posted: {permalink or media_id}")
        return

    if attempts == 0:
        sys.exit("The place catalog is used up. Add more places to places_tr.json.")
    sys.exit(f"No place could be rendered in {attempts} attempts; will try the next ones on the next run.")


def status():
    state = load_state()
    upcoming = list(queue(state))
    print(f"Posted: {len(state['posted'])}   Failed: {len(state['failed'])}   Left: {len(upcoming)}"
          f"  (~{len(upcoming) / 6:.0f} days at 6/day)")
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
    user_id = os.environ.get("IG_USER_ID", "")
    print(f"IG_ACCESS_TOKEN: {describe_token(token)}")
    print(f"IG_USER_ID: {len(user_id)} characters, {'all digits' if user_id.isdigit() else 'NOT all digits'}")
    try:
        me = instagram.whoami(token)
    except instagram.InstagramError as e:
        sys.exit(f"Token rejected by Instagram: {e}\n"
                 "Generate a new token in the Meta app (Instagram → API setup with Instagram business login → "
                 "Generate token) and store it again in the IG_ACCESS_TOKEN secret.")
    print(f"Token OK: @{me.get('username')} (user_id {me.get('user_id')}, {me.get('account_type')})")
    if user_id and user_id != str(me.get("user_id")):
        print(f"Warning: IG_USER_ID is {user_id}, but the token belongs to {me.get('user_id')}. "
              "Set IG_USER_ID to that number.")


def main():
    load_env()
    clean_secrets()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--dry-run", action="store_true")
    sub.add_parser("status")
    sub.add_parser("check")
    args = p.parse_args()
    if args.cmd == "run":
        run(args.dry_run)
    elif args.cmd == "status":
        status()
    else:
        check()


if __name__ == "__main__":
    main()
