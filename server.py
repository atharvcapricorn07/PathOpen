from flask import Flask, request, jsonify, send_from_directory
from PIL import Image
from supabase import create_client
import io, os, time, math, json, re, base64, requests as req
from flask_cors import CORS

app = Flask(__name__)
CORS(app, origins=["https://pathable-mu.vercel.app", "https://atharvcapricorn07-pathable.hf.space", "http://127.0.0.1:5000"])

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
GROQ_KEY     = os.environ.get("GROQ_API_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

DUPLICATE_RADIUS_METERS = 15
ABSOLUTE_BLOCK_METERS   = 5
VALID_LABELS = ["a staircase", "a ramp", "a wheelchair", "an elevator", "a curb cut", "a handrail"]

def haversine_meters(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi    = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

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
                "model": "meta-llama/llama-4-scout-17b-16e-instruct",
                "max_tokens": 256,
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

        match = re.search(r'\[.*?\]', raw, re.DOTALL)
        labels = json.loads(match.group()) if match else []
        labels = [l for l in labels if l in VALID_LABELS]

        result = {label: 1.0 for label in labels} if labels else {"_caption": "no features detected"}
        return jsonify(result)

    except Exception as e:
        print(f"ERROR in analyze_image: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/debug")
def debug():
    return jsonify({
        "supabase_url":    SUPABASE_URL,
        "key_length":      len(SUPABASE_KEY) if SUPABASE_KEY else 0,
        "key_starts_with": SUPABASE_KEY[:10] if SUPABASE_KEY else "EMPTY"
    })

@app.route("/markers", methods=["GET"])
def get_markers():
    lat    = float(request.args.get('lat'))
    lng    = float(request.args.get('lng'))
    radius = float(request.args.get('radius', 804))
    result = supabase.rpc("nearby_markers", {
        "lat": lat, "lng": lng, "radius_meters": radius
    }).execute()
    return jsonify(result.data)

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

        supabase.table("ada_markers").insert({
            "lat": lat, "lng": lng, "label": label,
            "image_url": image_url,
            "location":  f"POINT({lng} {lat})"
        }).execute()

        return jsonify({"success": True})

    except Exception as e:
        print(f"ERROR in save_marker: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=False)