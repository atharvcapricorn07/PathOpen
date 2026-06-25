from flask import Flask, request, jsonify, send_from_directory
from PIL import Image
from transformers import CLIPProcessor, CLIPModel
from supabase import create_client
import torch, io, os, time, math
from flask_cors import CORS

app = Flask(__name__)
CORS(app, origins=["https://pathable-mu.vercel.app", "https://atharvcapricorn07-pathable.hf.space", "http://127.0.0.1:5000"])

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

model     = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")

DUPLICATE_RADIUS_METERS = 15
ABSOLUTE_BLOCK_METERS   = 5

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
    file  = request.files['image']
    image = Image.open(io.BytesIO(file.read())).convert("RGB")
    w, h  = image.size
    bottom_half = image.crop((0, h // 2, w, h))

    positive_prompts = [
        "a flight of concrete or stone stairs with multiple steps going up or down",
        "a sloped wheelchair ramp leading to a building entrance",
        "a person sitting in a wheelchair or a parked wheelchair",
        "an elevator door or lift button panel on a wall",
        "a curb cut or dropped kerb at the edge of a sidewalk",
        "a metal handrail along stairs or a ramp",
    ]
    negative_prompts = [
        "a flat empty sidewalk with no steps or ramp",
        "the interior of a building lobby or hallway with no steps",
        "a street or road with no accessibility features",
        "a grassy field or park with no structures",
    ]
    label_map = {
        "a flight of concrete or stone stairs with multiple steps going up or down": "a staircase",
        "a sloped wheelchair ramp leading to a building entrance": "a ramp",
        "a person sitting in a wheelchair or a parked wheelchair": "a wheelchair",
        "an elevator door or lift button panel on a wall": "an elevator",
        "a curb cut or dropped kerb at the edge of a sidewalk": "a curb cut",
        "a metal handrail along stairs or a ramp": "a handrail",
    }

    all_prompts = positive_prompts + negative_prompts
    confidence  = {}

    for img in [image, bottom_half]:
        inputs  = processor(text=all_prompts, images=img, return_tensors="pt", padding=True)
        outputs = model(**inputs)
        probs   = outputs.logits_per_image.softmax(dim=1)[0].tolist()
        neg_mass = sum(probs[len(positive_prompts):])
        for i, prompt in enumerate(positive_prompts):
            raw      = probs[i]
            adjusted = raw - (0.5 * neg_mass * raw)
            short    = label_map[prompt]
            confidence[short] = max(confidence.get(short, 0), round(adjusted, 4))

    result = {k: v for k, v in confidence.items() if v >= 0.07}
    return jsonify(result)

@app.route("/debug")
def debug():
    return jsonify({
        "supabase_url":   SUPABASE_URL,
        "key_length":     len(SUPABASE_KEY) if SUPABASE_KEY else 0,
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
    import requests as req
    try:
        lat   = float(request.form.get('lat'))
        lng   = float(request.form.get('lng'))
        label = request.form.get('label', '').strip()
        file  = request.files.get('image')
        force = request.form.get('force', '0') == '1'

        file_bytes = file.read() if file else None

        # Duplicate check — distance + label only (embedding removed, too slow on CPU)
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

        # Image upload
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