# @gecmistenharita: Instagram Reels autopilot

This posts **4 animated, narrated English explainer Reels a day** ("how does X work?") with no manual work after setup. The earlier satellite-timelapse mode is still available (set `CONTENT: satellite` in the workflow).

Each run does the following:
1. Picks the next place from `places_world.json` (places around the world), or from [places_tr.json](places_tr.json) (50 places in Turkey) until the world catalog exists.
2. Downloads free satellite images (Landsat 1985→ / Sentinel-2 2016→), one cloud-free composite per year.
3. Renders a 1080×1920 Reel that plays **oldest map → newest map**. The hook question sits above the map, with a big year counter, a timeline bar, the logo and @gecmistenharita watermark, a "Takip et" card at the end, and music matched to the place's mood.
4. Writes the Turkish caption: hook question first, then SEO text, a comment CTA, the imagery credit and 5 hashtags.
5. Posts it as a Reel through the official Instagram API and records it in `state.json`.

It runs on **GitHub Actions** at 08:17, 13:17, 18:17 and 23:17 UTC, so your computer can stay off.

## Files

| File | What it is |
|---|---|
| `places_world.json`, `places_tr.json` | Place catalogs: coordinates, years, mood, two hook questions, story and hashtags. The world catalog is used once it exists |
| `timelapse.py` | Downloads imagery and renders the Reel |
| `captions.py` | Turkish caption / SEO text |
| `instagram.py` | Instagram Reels publishing. Instagram downloads the video from a public link, so each run briefly hosts the Reel on GitHub Pages |
| `autopilot.py` | Picks the next place, renders it, posts it, saves progress |
| `music.py` | Soundtrack per mood: your own tracks from `music/`, or a generated Turkish-style track |
| `music/` | Optional folder for your own royalty-free tracks (see `music/README.md`) |
| `make_logo.py`, `assets/` | The logo: `assets/logo.png` is the profile picture, `assets/logo_mark.png` is the Reel watermark |
| `.github/workflows/autopilot.yml` | Runs 4 times a day |
| `explainers.json` | Explainer scripts: title, hook, narration per beat, what each beat shows, caption, hashtags |
| `explain.py` | Animation engine: renders beats (title, split, merge, focus, flow, stat, list, compare), word-by-word subtitles, logo and outro |
| `tts.py` | English voiceover with word timings (Kokoro, open source, runs on the GitHub runner) |

## One-time setup

### 1. Instagram (about 10 minutes)
1. In the Instagram app: **Settings → Account type and tools → Switch to professional account** (Creator or Business).
2. Go to [developers.facebook.com](https://developers.facebook.com) → **My Apps → Create app**. Choose the use case **"Manage messaging & content on Instagram"**, which is the Instagram API with Instagram Login.
3. In the app, open **Instagram → API setup with Instagram login → Generate access tokens → Add account** and log in with the Instagram account.
4. Click **Generate token** and copy the **access token**. It is long and starts with `IGAA`. Don't use the app ID or app secret shown higher up the page.

The app can stay in Development mode. Because you're its admin, posting to your own account needs no App Review.

### 2. GitHub (about 10 minutes)
1. Create a repository and upload these files. A **public** repo gets unlimited free Actions minutes. A private repo gets 2,000 free minutes a month, which 6 renders a day can use up. Your secrets stay hidden either way.
2. **Settings → Secrets and variables → Actions → New repository secret**. Add:
   - `IG_ACCESS_TOKEN`: the access token
   - `GH_PAT`: a [fine-grained personal access token](https://github.com/settings/personal-access-tokens/new) for this repository only, with the permission **Secrets: Read and write**. The autopilot needs it to save the refreshed Instagram token every week. Without it, the Instagram token expires after 60 days.
3. Open the **Actions** tab, then **Instagram autopilot → Run workflow**, and pick a **mode**:
   - **check**: tests the Instagram token in about a minute, no rendering
   - **dry-run**: renders a Reel without posting. Download it from the run's artifacts
   - **post**: renders and posts. This is also what the schedule does
4. After the first successful **post**, the schedule takes over.

## Running on your computer instead

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env              # then fill in IG_ACCESS_TOKEN
.venv/bin/python autopilot.py check            # verifies the token
.venv/bin/python autopilot.py render --dry-run # renders the next Reel, posts nothing
.venv/bin/python autopilot.py render          # renders the next Reel into work/pending/
.venv/bin/python autopilot.py publish --video-url https://…/reel.mp4   # Instagram downloads it from a public URL
.venv/bin/python autopilot.py status           # what's posted and what's next
```

## How long the catalog lasts
- Each place is used twice: round 1 is a close-up with the first hook, round 2 is a wider view with the second hook. At 24 a day, 100 places last about 8 days.
- If a place has too few cloud-free years, the run skips it and posts the next one.
- When the catalog runs out, the workflow fails and GitHub emails you. Add more entries to `places_world.json` in the same format to keep going.
- `python autopilot.py status` shows which catalog is in use and how many days are left.

## Notes
- Imagery is free. **Credit is required**, and every caption and video already includes it.
- Instagram's API can't add trending or licensed music. Each Reel gets a generated soundtrack for its mood (saz-like strings on makam scales, darbuka rhythms), or one of your own royalty-free tracks from `music/<mood>/`. Moods: calm (drying lakes), water (dams, deltas), city, build (airports, bridges, ports), drama (fires, mines), mountain.
- Hashtags are capped at 5.
- The Instagram API can't change the profile picture. Upload `assets/logo.png` yourself.
- A full 40-year render takes about 5–15 minutes depending on network speed.
