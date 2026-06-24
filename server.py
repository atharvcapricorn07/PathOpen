from flask import Flask, request, jsonify, send_from_directory
from PIL import Image
from transformers import CLIPProcessor, CLIPModel
from supabase import create_client
import torch, io, os, time, math
from flask_cors import CORS

app = Flask(__name__)
CORS(app, origins=["https://pathable-mu.vercel.app", "https://atharvcapricorn07-pathable.hf.space", "http://127.0.0.1:5000"])

# Supabase setup
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# CLIP model
model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")

DUPLICATE_RADIUS_METERS = 9  # ~30 feet

def haversine_meters(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
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
    file = request.files['image']
    image = Image.open(io.BytesIO(file.read())).convert("RGB")

    texts = ["a staircase", "a ramp", "a wheelchair", "an elevator", "no ADA feature"]
    inputs = processor(text=texts, images=image, return_tensors="pt", padding=True)
    outputs = model(**inputs)
    probs = outputs.logits_per_image.softmax(dim=1)[0].tolist()

    confidence = {
        t: round(p, 4) for t, p in zip(texts, probs) if p >= 0.10
    }

    return jsonify(confidence)

@app.route("/debug")
def debug():
    return jsonify({
        "supabase_url": SUPABASE_URL,
        "key_length": len(SUPABASE_KEY) if SUPABASE_KEY else 0,
        "key_starts_with": SUPABASE_KEY[:10] if SUPABASE_KEY else "EMPTY"
    })

@app.route("/markers", methods=["GET"])
def get_markers():
    lat = float(request.args.get('lat'))
    lng = float(request.args.get('lng'))
    radius = float(request.args.get('radius', 804))

    result = supabase.rpc("nearby_markers", {
        "lat": lat,
        "lng": lng,
        "radius_meters": radius
    }).execute()

    return jsonify(result.data)

@app.route("/save-marker", methods=["POST"])
def save_marker():
    import requests as req
    try:
        lat = float(request.form.get('lat'))
        lng = float(request.form.get('lng'))
        label = request.form.get('label', '').strip()
        file = request.files.get('image')

        # --- Duplicate check ---
        delta = 0.0001  # ~11m bounding box, wider than our 9m threshold
        existing = (
            supabase.table("ada_markers")
            .select("id, label, lat, lng")
            .gte("lat", lat - delta)
            .lte("lat", lat + delta)
            .gte("lng", lng - delta)
            .lte("lng", lng + delta)
            .execute()
        )
        for m in (existing.data or []):
            dist = haversine_meters(lat, lng, float(m['lat']), float(m['lng']))
            if dist <= DUPLICATE_RADIUS_METERS and m['label'] == label:
                return jsonify({
                    "error": f"A '{label}' marker already exists within 30 feet of this spot.",
                    "duplicate": True
                }), 409

        # --- Image upload ---
        image_url = None
        if file:
            file_bytes = file.read()
            filename = f"{label.replace(' ', '_')}_{int(abs(lat)*1000)}_{int(abs(lng)*1000)}_{int(time.time())}.jpg"
            storage_url = f"{SUPABASE_URL}/storage/v1/object/ada-images/{filename}"
            headers = {
                "Authorization": f"Bearer {SUPABASE_KEY.strip()}",
                "Content-Type": "image/jpeg"
            }
            r = req.post(storage_url, data=file_bytes, headers=headers)
            print(f"Storage status: {r.status_code}, response: {r.text}")
            if r.status_code in [200, 201]:
                image_url = f"{SUPABASE_URL}/storage/v1/object/public/ada-images/{filename}"

        # --- Guard: don't insert if image upload failed ---
        if file and image_url is None:
            return jsonify({"error": "Image upload failed. Marker not saved."}), 500

        # --- Insert marker ---
        supabase.table("ada_markers").insert({
            "lat": lat,
            "lng": lng,
            "label": label,
            "image_url": image_url,
            "location": f"POINT({lng} {lat})"
        }).execute()

        return jsonify({"success": True})
    except Exception as e:
        print(f"ERROR in save_marker: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=False)