from flask import Flask, request, jsonify, send_from_directory
from PIL import Image
from transformers import BlipProcessor, BlipForConditionalGeneration
from supabase import create_client
import torch, io, os, time, math
from flask_cors import CORS

app = Flask(__name__)
CORS(app, origins=["https://pathable-mu.vercel.app", "https://atharvcapricorn07-pathable.hf.space", "http://127.0.0.1:5000"])

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

processor = BlipProcessor.from_pretrained("Salesforce/blip-image-captioning-base")
model     = BlipForConditionalGeneration.from_pretrained("Salesforce/blip-image-captioning-base")
model.eval()

DUPLICATE_RADIUS_METERS = 15
ABSOLUTE_BLOCK_METERS   = 5

def haversine_meters(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi    = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

# Keyword sets for each ADA label — match against BLIP caption
LABEL_KEYWORDS = {
    "a staircase":  ["stair", "stairs", "staircase", "steps", "step"],
    "a ramp":       ["ramp", "slope", "sloped", "incline", "wheelchair ramp"],
    "a wheelchair": ["wheelchair", "wheel chair", "mobility"],
    "an elevator":  ["elevator", "lift", "elevator door", "elevator button"],
    "a curb cut":   ["curb cut", "curb", "kerb", "dropped curb", "sidewalk cut"],
    "a handrail":   ["handrail", "railing", "rail", "bannister", "banister"],
}

def caption_to_labels(caption: str) -> dict:
    caption_lower = caption.lower()
    result = {}
    for label, keywords in LABEL_KEYWORDS.items():
        for kw in keywords:
            if kw in caption_lower:
                result[label] = 1.0
                break
    return result

def generate_caption(image: Image.Image) -> str:
    inputs = processor(image, return_tensors="pt")
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=50)
    return processor.decode(out[0], skip_special_tokens=True)

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

    # Caption both full image and bottom half, merge results
    caption_full   = generate_caption(image)
    caption_bottom = generate_caption(bottom_half)
    print(f"BLIP caption (full): {caption_full}")
    print(f"BLIP caption (bottom): {caption_bottom}")

    labels_full   = caption_to_labels(caption_full)
    labels_bottom = caption_to_labels(caption_bottom)

    # Merge — if either pass finds a label, include it
    merged = {**labels_full, **labels_bottom}

    # If nothing matched, return the raw caption so frontend can show it
    if not merged:
        merged["_caption"] = caption_full

    return jsonify(merged)

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
    import requests as req
    try:
        lat   = float(request.form.get('lat'))
        lng   = float(request.form.get('lng'))
        label = request.form.get('label', '').strip()
        file  = request.files.get('image')
        force = request.form.get('force', '0') == '1'

        file_bytes = file.read() if file else None

        # Duplicate check — distance + label only
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