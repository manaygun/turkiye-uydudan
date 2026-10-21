"""Publish Reels through the Instagram API with Instagram Login.

Flow: create a resumable REELS container -> upload the file to rupload.facebook.com
-> wait until Instagram finishes processing -> publish. No public video URL needed.
"""

import os
import time

import requests

API_VERSION = os.environ.get("IG_API_VERSION", "v23.0")
BASE = f"https://graph.instagram.com/{API_VERSION}"


class InstagramError(Exception):
    pass


def _check(resp):
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code >= 400 or "error" in data:
        err = data.get("error", resp.text[:300])
        raise InstagramError(f"HTTP {resp.status_code}: {err}")
    return data


def whoami(token):
    return _check(requests.get(f"{BASE}/me", params={
        "fields": "user_id,username,account_type", "access_token": token}, timeout=30))


def publish_reel(user_id, token, video_path, caption, thumb_offset_ms=None, log=print):
    """Returns (media_id, permalink)."""
    params = {
        "media_type": "REELS",
        "upload_type": "resumable",
        "caption": caption,
        "share_to_feed": "true",
        "access_token": token,
    }
    if thumb_offset_ms is not None:
        params["thumb_offset"] = str(int(thumb_offset_ms))
    container = _check(requests.post(f"{BASE}/{user_id}/media", data=params, timeout=60))["id"]
    log(f"  container {container} created, uploading…")

    size = os.path.getsize(video_path)
    with open(video_path, "rb") as f:
        _check(requests.post(
            f"https://rupload.facebook.com/ig-api-upload/{API_VERSION}/{container}",
            headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)},
            data=f, timeout=600))

    for _ in range(90):            # up to ~15 min of processing
        status = _check(requests.get(f"{BASE}/{container}", params={
            "fields": "status_code,status", "access_token": token}, timeout=30))
        code = status.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise InstagramError(f"processing failed: {status}")
        time.sleep(10)
    else:
        raise InstagramError("timed out waiting for Instagram to process the video")

    media_id = _check(requests.post(f"{BASE}/{user_id}/media_publish", data={
        "creation_id": container, "access_token": token}, timeout=60))["id"]
    try:
        permalink = _check(requests.get(f"{BASE}/{media_id}", params={
            "fields": "permalink", "access_token": token}, timeout=30)).get("permalink")
    except InstagramError:
        permalink = None
    return media_id, permalink


def refresh_token(token):
    """Long-lived tokens last 60 days; refreshing returns a new 60-day token."""
    data = _check(requests.get("https://graph.instagram.com/refresh_access_token", params={
        "grant_type": "ig_refresh_token", "access_token": token}, timeout=30))
    return data["access_token"]
