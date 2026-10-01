# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

PathOpen (also branded "Pathable") is a crowdsourced accessibility-navigation PWA: a Leaflet map with walking routes, plus user-submitted markers for accessibility features (ramps, elevators, curb cuts, handrails, staircases, etc.). Users add markers by uploading a photo; a vision LLM detects the feature and later re-verifies it.

There is no build step, bundler, linter, or test suite. The front end is plain HTML/CSS/JS loading libraries from unpkg; the back end is a single Flask file (`server.py`).

## Running and deploying

```bash
pip install -r requirements.txt
export SUPABASE_URL=... SUPABASE_KEY=... GROQ_API_KEY=... ADMIN_KEY=...
python server.py                                  # Flask dev server on 127.0.0.1:5000 (serves static files too)
gunicorn server:app --bind 0.0.0.0:7860 --timeout 120   # what production runs
```

- `server.py` calls `create_client(SUPABASE_URL, SUPABASE_KEY)` at import time, so it won't start without Supabase env vars.
- **The front end hardcodes the production API base** (`https://atharvcapricorn07-pathopen.hf.space`) in `js/script.js` (several `fetch` calls), `html/ai.html` (`HF`), and `dashboard.html` (`BASE`). Running locally still talks to the production backend unless those are changed.
- Deployment is a Hugging Face Docker Space. The YAML front matter in `README.md` is the Space config; `Dockerfile` runs gunicorn on port 7860. Pushing `main` to the `hf` remote triggers a rebuild; `origin` is GitHub. CORS origins are whitelisted at the top of `server.py`.

Ops scripts (hit the production server; require `PATHOPEN_ADMIN_KEY` in the environment):
- `./stats.sh`: usage stats, bug reports, and flagged markers awaiting review.
- `./verify-now.sh`: loops `POST /verify-pending` until the unverified-marker backlog is empty.

## Architecture

**Pages:**
- `index.html` + `js/script.js`: main map: CARTO tiles, `leaflet-rotate` for compass heading, `leaflet-routing-machine` against the public OSRM server, geocoding, turn-by-turn nav mode with rerouting and proximity callouts.
- `html/ai.html`: photo upload → `/analyze` → pick a label → `/save-marker`.
- `dashboard.html`: admin ops UI (key kept in `localStorage`).

**Data (Supabase):**
- Tables: `ada_markers`, `device_visits`, `route_events`, `bug_reports`.
- Images: stored in the `ada-images` storage bucket. They are uploaded via raw REST to `/storage/v1/object/...`, not via the supabase client.

**Marker lifecycle (spans `server.py`, `html/ai.html`, `js/script.js`):**
1. `/save-marker` dedupes first: it rejects anything within `ABSOLUTE_BLOCK_METERS` (5 m), and anything with the same label within `DUPLICATE_RADIUS_METERS` (15 m). The response is a 409 with `duplicate: true`; the client can resubmit with `force=1`.
2. After insert, a daemon thread runs `run_verification_and_flag`, which asks the vision model whether the photo really shows the label.
   - **Mismatch:** sets `flagged=true` + `flag_reason`.
   - **Verdict either way:** sets `verified_at`.
   - **Technical failure:** leaves `verified_at` null on purpose (fail closed), so the `/verify-pending` sweep (25 per call, run by an external 3am cron) retries it later.
3. Public endpoints (`/markers`, `/markers/all`) filter out `flagged` markers. Admins resolve them via `/flagged/<id>/resolve` with `keep` or `delete`.
4. Radius queries use a lat/lng bounding-box prefilter in Supabase, then exact haversine in Python (`haversine_meters`). Don't move this back into a SQL RPC.

**AI:**
- Groq's OpenAI-compatible API, vision model set once in `VISION_MODEL` in `server.py`. The comment there records model deprecation history.
- Both `/analyze` and verification resize images to 800px JPEG before sending.

**Admin auth:** endpoints that call `require_admin()` check the `X-Admin-Key` header against the `ADMIN_KEY` env var.

## Things to keep in sync

- **Marker labels** are raw strings with articles ("a ramp", "an elevator"). They're defined in `VALID_LABELS` (`server.py`), the detection list/prompt in `html/ai.html`, and `getMarkerColor`/`isObstacleLabel` in `js/script.js` ("a blocked path" exists only on the front end). `formatLabel` strips the article for display.
- **The production backend URL** is repeated across the three pages and both ops scripts.
- **`/markers` vs `/markers/all`:** the global "all markers" layer (`allMarkersLayer`, from `/markers/all`) is deliberately separate from the per-route nearby layer (`adaMarkers`, from `/markers`). Toggling one must not affect the other.
