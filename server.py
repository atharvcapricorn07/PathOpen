from flask import Flask, request, jsonify, send_from_directory
from PIL import Image
from supabase import create_client
import io, os, time, math, json, re, base64, threading, requests as req
from datetime import datetime, timezone
from flask_cors import CORS

app = Flask(__name__)
CORS(app, origins=["https://pathable-mu.vercel.app", "https://atharvcapricorn07-accessscout.hf.space", "http://127.0.0.1:5000"])

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
GROQ_KEY     = os.environ.get("GROQ_API_KEY")
ADMIN_KEY    = os.environ.get("ADMIN_KEY")  # required to hit /flagged, /verify-pending, etc.

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

DUPLICATE_RADIUS_METERS = 15
ABSOLUTE_BLOCK_METERS   = 5
VALID_LABELS = ["a staircase", "a ramp", "a wheelchair", "an elevator", "a curb cut", "a handrail"]

# Groq deprecated meta-llama/llama-4-maverick-17b-128e-instruct on March 9, 2026,
# and meta-llama/llama-4-scout-17b-16e-instruct is scheduled to shut down July 17, 2026.
# Groq's own migration guidance recommends openai/gpt-oss-120b as the general
# replacement, but that model is TEXT-ONLY (no vision support) as of this writing.
# For vision/multimodal workloads like ours, Groq's documented replacement is
# qwen/qwen3.6-27b (currently a preview-tier model on Groq, but the only
# vision-capable option in the migration path). Centralized here so both
# vision call sites stay in sync if this needs to change again.
VISION_MODEL = "qwen/qwen3.6-27b"

def haversine_meters(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi    = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def require_admin():
    """Returns True if the request carries the correct admin key. Used to gate
    review/moderation endpoints that aren't meant for regular app users."""
    if not ADMIN_KEY:
        return False  # fail closed if no key has been configured yet
    supplied = request.headers.get("X-Admin-Key", "")
    return supplied == ADMIN_KEY

@app.route("/")
def index():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "index.html")

@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), filename)

@app.route("/analyze", methods=["POST"])
def analyze_image():
    try:
        file  = request.files['image']
        image = Image.open(io.BytesIO(file.read())).convert("RGB")
        image.thumbnail((800, 800))
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=85)
        img_b64 = base64.standard_b64encode(buf.getvalue()).decode("utf-8")

        prompt = (
            "You are an ADA accessibility feature detector. "
            "Look at this image and identify which of the following are CLEARLY and VISIBLY present. "
            "Only include features you can actually see — do not guess.\n\n"
            "Features to check:\n"
            "- a staircase (steps going up or down)\n"
            "- a ramp (sloped surface for wheelchair access, distinct from stairs)\n"
            "- a wheelchair (person sitting in a wheelchair)\n"
            "- an elevator (elevator doors or call buttons)\n"
            "- a curb cut (small ramp cut into a sidewalk curb at street level)\n"
            "- a handrail (railing mounted next to stairs or a ramp)\n\n"
            "Reply ONLY with a JSON array using exact label names. "
            'Example: ["a staircase", "a handrail"]\n'
            "If nothing is present, reply: []"
        )

        response = req.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": VISION_MODEL,
                "max_tokens": 512,
                "reasoning_effort": "none",  # qwen3.6 is a reasoning model; skip <think> to save tokens
                "messages": [{
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }]
            }
        )

        raw = response.json()["choices"][0]["message"]["content"].strip()
        print(f"Groq response: {raw}")

        # Defensively strip any reasoning block, in case reasoning_effort is
        # ignored by the API and the model still emits <think>...</think>.
        raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()

        match = re.search(r'\[.*?\]', raw, re.DOTALL)
        labels = json.loads(match.group()) if match else []
        labels = [l for l in labels if l in VALID_LABELS]

        result = {label: 1.0 for label in labels} if labels else {"_caption": "no features detected"}
        return jsonify(result)

    except Exception as e:
        print(f"ERROR in analyze_image: {e}")
        return jsonify({"error": str(e)}), 500

def verify_image_matches_label(image_bytes, label):
    """Ask the vision model whether the image genuinely shows `label`.
    Returns (matches, reason, error):
      - matches: True/False when the model gave a real verdict, None if the check
        itself failed (network error, bad response, etc.)
      - reason: short explanation string (empty on error)
      - error: True if this was a technical failure, not a model verdict

    IMPORTANT: this now fails CLOSED in the sense that a technical error is never
    silently treated as "matches". Callers must check `error` and leave the marker
    unverified (don't set verified_at) so it gets retried later, instead of
    marking a failed check as a clean pass."""
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        image.thumbnail((800, 800))
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=85)
        img_b64 = base64.standard_b64encode(buf.getvalue()).decode("utf-8")

        prompt = (
            f"Someone tagged this photo as showing {label}. "
            "Look very carefully at the actual visible details — do not assume a feature exists just "
            "because of the general setting (e.g. don't assume a ramp exists just because you see a "
            "building entrance; don't assume a handrail exists just because you see any railing). "
            "Do not conclude a ramp is present based on color, paint, or texture alone — a yellow, "
            "textured, or painted strip on otherwise flat ground is tactile paving or a floor marking, "
            "NOT a ramp, unless the surface itself visibly rises or slopes in elevation. "
            "Only say true if you can point to a specific, clearly visible detail that matches. "
            'Reply ONLY with JSON in this exact form: {"matches": true or false, "reason": "one short sentence naming the specific detail you saw or did not see"}'
        )

        response = req.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": VISION_MODEL,
                "max_tokens": 400,
                "reasoning_effort": "none",  # qwen3.6 is a reasoning model; skip <think> to save tokens
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                        {"type": "text", "text": prompt}
                    ]
                }]
            },
            timeout=25
        )

        if not response.ok:
            err_detail = f"Groq API returned {response.status_code}: {response.text[:300]}"
            print(f"ERROR in verify_image_matches_label: {err_detail}")
            return None, err_detail, True

        raw = response.json()["choices"][0]["message"]["content"].strip()
        # Defensively strip any reasoning block, in case reasoning_effort is
        # ignored by the API and the model still emits <think>...</think>.
        raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()
        match = re.search(r'\{.*?\}', raw, re.DOTALL)
        if not match:
            err_detail = f"no JSON found in model response: {raw[:300]!r}"
            print(f"ERROR in verify_image_matches_label: {err_detail}")
            return None, err_detail, True

        parsed = json.loads(match.group())
        return bool(parsed.get("matches", True)), str(parsed.get("reason", ""))[:300], False

    except Exception as e:
        err_detail = f"{type(e).__name__}: {e}"
        print(f"ERROR in verify_image_matches_label: {err_detail}")
        return None, err_detail[:300], True  # technical failure — caller must NOT treat this as verified

def run_verification_and_flag(marker_id, image_bytes, label):
    """Runs in a background thread so /save-marker can return to the user
    immediately without waiting on a second Groq call."""
    try:
        matches, reason, error = verify_image_matches_label(image_bytes, label)
        if error:
            # Technical failure, not a real verdict — leave verified_at unset
            # so the /verify-pending sweep picks this marker up and retries it.
            print(f"Verification for marker {marker_id} failed technically; leaving unverified for retry.")
            return
        update = {"verified_at": datetime.now(timezone.utc).isoformat()}
        if not matches:
            update["flagged"] = True
            update["flag_reason"] = reason or f"AI did not detect '{label}' in this photo."
        supabase.table("ada_markers").update(update).eq("id", marker_id).execute()
    except Exception as e:
        print(f"ERROR in run_verification_and_flag for marker {marker_id}: {e}")

@app.route("/debug")
def debug():
    return jsonify({
        "supabase_url":    SUPABASE_URL,
        "key_length":      len(SUPABASE_KEY) if SUPABASE_KEY else 0,
        "key_starts_with": SUPABASE_KEY[:10] if SUPABASE_KEY else "EMPTY"
    })

@app.route("/markers", methods=["GET"])
def get_markers():
    try:
        lat    = float(request.args.get('lat'))
        lng    = float(request.args.get('lng'))
        radius = float(request.args.get('radius', 804))

        # Cheap bounding-box pre-filter (degrees), then precise great-circle
        # distance check below. This replaces the old nearby_markers RPC call
        # so the radius logic lives in one auditable place, not hidden in SQL.
        delta = (radius / 111000) * 1.5  # generous pad, refined by haversine next
        rows = (
            supabase.table("ada_markers")
            .select("id, lat, lng, label, image_url, flagged")
            .gte("lat", lat - delta).lte("lat", lat + delta)
            .gte("lng", lng - delta).lte("lng", lng + delta)
            .execute()
        )

        nearby = []
        for m in (rows.data or []):
            if m.get("flagged"):
                continue  # hide anything pending review from the public map
            dist = haversine_meters(lat, lng, float(m["lat"]), float(m["lng"]))
            if dist <= radius:
                nearby.append(m)

        return jsonify(nearby)
    except Exception as e:
        print(f"ERROR in get_markers: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/save-marker", methods=["POST"])
def save_marker():
    try:
        lat   = float(request.form.get('lat'))
        lng   = float(request.form.get('lng'))
        label = request.form.get('label', '').strip()
        file  = request.files.get('image')
        force = request.form.get('force', '0') == '1'

        file_bytes = file.read() if file else None

        if not force:
            delta    = 0.0002
            existing = (
                supabase.table("ada_markers")
                .select("id, label, lat, lng, image_url")
                .gte("lat", lat - delta).lte("lat", lat + delta)
                .gte("lng", lng - delta).lte("lng", lng + delta)
                .execute()
            )
            for m in (existing.data or []):
                dist = haversine_meters(lat, lng, float(m['lat']), float(m['lng']))
                if dist <= ABSOLUTE_BLOCK_METERS:
                    return jsonify({
                        "error": "A marker already exists very close to this spot.",
                        "duplicate": True,
                        "existing": {
                            "id": m["id"], "label": m["label"],
                            "image_url": m.get("image_url"), "distance_m": round(dist, 1)
                        }
                    }), 409
                if dist > DUPLICATE_RADIUS_METERS:
                    continue
                if m['label'] == label:
                    return jsonify({
                        "error": f"A '{label}' marker already exists nearby.",
                        "duplicate": True,
                        "existing": {
                            "id": m["id"], "label": m["label"],
                            "image_url": m.get("image_url"), "distance_m": round(dist, 1)
                        }
                    }), 409

        image_url = None
        if file_bytes:
            filename    = f"{label.replace(' ', '_')}_{int(abs(lat)*1000)}_{int(abs(lng)*1000)}_{int(time.time())}.jpg"
            storage_url = f"{SUPABASE_URL}/storage/v1/object/ada-images/{filename}"
            headers     = {
                "Authorization": f"Bearer {SUPABASE_KEY.strip()}",
                "Content-Type":  "image/jpeg"
            }
            r = req.post(storage_url, data=file_bytes, headers=headers)
            print(f"Storage status: {r.status_code}, response: {r.text}")
            if r.status_code in [200, 201]:
                image_url = f"{SUPABASE_URL}/storage/v1/object/public/ada-images/{filename}"
            else:
                return jsonify({"error": f"Storage failed: {r.status_code} - {r.text}"}), 500

        insert_result = supabase.table("ada_markers").insert({
            "lat": lat, "lng": lng, "label": label,
            "image_url": image_url,
            "location":  f"POINT({lng} {lat})"
        }).execute()

        # Fire off a background AI re-check: does the photo actually show what
        # was tagged? Doesn't block the response — the user's save still feels instant.
        if file_bytes and insert_result.data:
            new_id = insert_result.data[0]["id"]
            threading.Thread(
                target=run_verification_and_flag,
                args=(new_id, file_bytes, label),
                daemon=True
            ).start()

        return jsonify({"success": True})

    except Exception as e:
        print(f"ERROR in save_marker: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/track", methods=["POST"])
def track_device():
    try:
        data = request.get_json(silent=True) or {}
        device_id = data.get("device_id")
        if not device_id:
            return jsonify({"error": "missing device_id"}), 400

        existing = (
            supabase.table("device_visits")
            .select("visit_count")
            .eq("device_id", device_id)
            .execute()
        )

        now_iso = datetime.now(timezone.utc).isoformat()

        if existing.data:
            current_count = existing.data[0]["visit_count"]
            supabase.table("device_visits").update({
                "last_seen": now_iso,
                "visit_count": current_count + 1
            }).eq("device_id", device_id).execute()
        else:
            supabase.table("device_visits").insert({
                "device_id": device_id,
                "first_seen": now_iso,
                "last_seen": now_iso
            }).execute()

        return jsonify({"success": True})

    except Exception as e:
        print(f"ERROR in track_device: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/report-bug", methods=["POST"])
def report_bug():
    try:
        data = request.get_json(silent=True) or {}
        message = (data.get("message") or "").strip()
        device_id = data.get("device_id", "")
        page_url = data.get("page_url", "")
        user_agent = data.get("user_agent", "")

        if not message:
            return jsonify({"error": "message is required"}), 400

        supabase.table("bug_reports").insert({
            "device_id": device_id,
            "message": message,
            "page_url": page_url,
            "user_agent": user_agent
        }).execute()

        return jsonify({"success": True})

    except Exception as e:
        print(f"ERROR in report_bug: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/track-route", methods=["POST"])
def track_route():
    try:
        supabase.table("route_events").insert({}).execute()
        return jsonify({"success": True})
    except Exception as e:
        print(f"ERROR in track_route: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/bugs")
def bugs():
    try:
        result = (
            supabase.table("bug_reports")
            .select("id, created_at, message, device_id, page_url, user_agent")
            .order("created_at", desc=True)
            .execute()
        )
        return jsonify(result.data)
    except Exception as e:
        print(f"ERROR in bugs: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/stats")
def stats():
    try:
        unique_devices = supabase.table("device_visits").select("device_id", count="exact").execute()

        returning_devices = (
            supabase.table("device_visits")
            .select("device_id", count="exact")
            .gt("visit_count", 1)
            .execute()
        )

        total_markers = supabase.table("ada_markers").select("id", count="exact").execute()

        routes_generated = supabase.table("route_events").select("id", count="exact").execute()

        bug_reports = supabase.table("bug_reports").select("id", count="exact").execute()

        label_counts = {}
        all_markers = supabase.table("ada_markers").select("label").execute()
        for row in (all_markers.data or []):
            label = row.get("label", "unknown")
            label_counts[label] = label_counts.get(label, 0) + 1

        return jsonify({
            "unique_devices": unique_devices.count,
            "returning_devices": returning_devices.count,
            "total_markers_submitted": total_markers.count,
            "routes_generated": routes_generated.count,
            "bug_reports": bug_reports.count,
            "markers_by_label": label_counts
        })
    except Exception as e:
        print(f"ERROR in stats: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/flagged")
def flagged_markers():
    if not require_admin():
        return jsonify({"error": "unauthorized"}), 403
    try:
        result = (
            supabase.table("ada_markers")
            .select("id, created_at, lat, lng, label, image_url, flag_reason")
            .eq("flagged", True)
            .order("created_at", desc=True)
            .execute()
        )
        return jsonify(result.data)
    except Exception as e:
        print(f"ERROR in flagged_markers: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/flagged/<marker_id>/resolve", methods=["POST"])
def resolve_flagged(marker_id):
    if not require_admin():
        return jsonify({"error": "unauthorized"}), 403
    try:
        data = request.get_json(silent=True) or {}
        action = data.get("action")  # "delete" or "keep"

        if action == "delete":
            supabase.table("ada_markers").delete().eq("id", marker_id).execute()
            return jsonify({"success": True, "action": "deleted"})
        elif action == "keep":
            supabase.table("ada_markers").update({
                "flagged": False,
                "flag_reason": None
            }).eq("id", marker_id).execute()
            return jsonify({"success": True, "action": "kept"})
        else:
            return jsonify({"error": "action must be 'delete' or 'keep'"}), 400
    except Exception as e:
        print(f"ERROR in resolve_flagged: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/admin/markers")
def admin_all_markers():
    """Lists every marker with its verification status — useful for finding
    the ID of a specific marker you want to force-check or investigate."""
    if not require_admin():
        return jsonify({"error": "unauthorized"}), 403
    try:
        result = (
            supabase.table("ada_markers")
            .select("id, created_at, lat, lng, label, image_url, flagged, flag_reason, verified_at")
            .order("created_at", desc=True)
            .execute()
        )
        return jsonify(result.data)
    except Exception as e:
        print(f"ERROR in admin_all_markers: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/admin/markers/<marker_id>", methods=["DELETE"])
def admin_delete_marker(marker_id):
    """Directly deletes a marker regardless of its flagged/verified status.
    For cases where you've manually confirmed something's wrong and don't
    want to wait on the AI to agree first."""
    if not require_admin():
        return jsonify({"error": "unauthorized"}), 403
    try:
        supabase.table("ada_markers").delete().eq("id", marker_id).execute()
        return jsonify({"success": True, "deleted": marker_id})
    except Exception as e:
        print(f"ERROR in admin_delete_marker: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/verify-marker/<marker_id>", methods=["POST"])
def verify_single_marker(marker_id):
    """Force an immediate, synchronous re-check of one specific marker,
    regardless of whether it's already been verified. Returns the AI's
    actual verdict directly, instead of just updating quietly in the background —
    useful for debugging a specific marker you're suspicious of right now."""
    if not require_admin():
        return jsonify({"error": "unauthorized"}), 403
    try:
        row = (
            supabase.table("ada_markers")
            .select("id, label, image_url")
            .eq("id", marker_id)
            .single()
            .execute()
        )
        m = row.data
        if not m or not m.get("image_url"):
            return jsonify({"error": "marker not found or has no image"}), 404

        img_res = req.get(m["image_url"], timeout=15)
        if not img_res.ok:
            return jsonify({"error": f"could not fetch image: {img_res.status_code}"}), 500

        matches, reason, error = verify_image_matches_label(img_res.content, m["label"])
        if error:
            # Technical failure — do NOT touch verified_at/flagged, and surface
            # the actual underlying error detail (Groq status, bad JSON, exception
            # message) so this is debuggable straight from the API response,
            # without needing to dig through HF's log viewer.
            return jsonify({
                "marker_id": marker_id,
                "label": m["label"],
                "error": "verification check failed, marker left unverified",
                "detail": reason
            }), 502

        update = {"verified_at": datetime.now(timezone.utc).isoformat()}
        if not matches:
            update["flagged"] = True
            update["flag_reason"] = reason or f"AI did not detect '{m['label']}' in this photo."
        else:
            update["flagged"] = False
            update["flag_reason"] = None
        supabase.table("ada_markers").update(update).eq("id", marker_id).execute()

        return jsonify({"marker_id": marker_id, "label": m["label"], "matches": matches, "reason": reason})
    except Exception as e:
        print(f"ERROR in verify_single_marker: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/verify-pending", methods=["POST"])
def verify_pending():
    """Periodic sweep: re-checks any marker that has never been through the
    AI verification pass (verified_at is null) — including markers saved
    before this feature existed. Trigger this on a schedule (cron-job.org,
    a GitHub Actions scheduled workflow, etc.) hitting this endpoint with
    the X-Admin-Key header set."""
    if not require_admin():
        return jsonify({"error": "unauthorized"}), 403
    try:
        pending = (
            supabase.table("ada_markers")
            .select("id, label, image_url")
            .filter("verified_at", "is", "null")
            .filter("image_url", "not.is", "null")
            .limit(25)  # keep each sweep small and fast
            .execute()
        )

        checked = 0
        flagged = 0
        failed = 0
        failures = []  # {marker_id, detail} for anything that errored this sweep
        for m in (pending.data or []):
            try:
                img_res = req.get(m["image_url"], timeout=15)
                if not img_res.ok:
                    failed += 1
                    failures.append({"marker_id": m["id"], "detail": f"could not fetch image: {img_res.status_code}"})
                    continue
                matches, reason, error = verify_image_matches_label(img_res.content, m["label"])
                if error:
                    # Leave verified_at null so this marker gets retried on the
                    # next sweep instead of silently being marked as passing.
                    failed += 1
                    failures.append({"marker_id": m["id"], "detail": reason})
                    continue
                update = {"verified_at": datetime.now(timezone.utc).isoformat()}
                if not matches:
                    update["flagged"] = True
                    update["flag_reason"] = reason or f"AI did not detect '{m['label']}' in this photo."
                    flagged += 1
                supabase.table("ada_markers").update(update).eq("id", m["id"]).execute()
                checked += 1
            except Exception as inner_e:
                print(f"ERROR verifying marker {m.get('id')}: {inner_e}")
                failed += 1
                failures.append({"marker_id": m.get("id"), "detail": f"{type(inner_e).__name__}: {inner_e}"})

        return jsonify({
            "checked": checked,
            "newly_flagged": flagged,
            "failed_will_retry": failed,
            "failures": failures
        })
    except Exception as e:
        print(f"ERROR in verify_pending: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=False)