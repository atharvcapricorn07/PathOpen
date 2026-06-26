from flask import Flask, request, jsonify, send_from_directory
from PIL import Image
from transformers import BlipProcessor, BlipForQuestionAnswering
from supabase import create_client
import torch, io, os, time, math
from flask_cors import CORS

app = Flask(__name__)
CORS(app, origins=["https://pathable-mu.vercel.app", "https://atharvcapricorn07-pathable.hf.space", "http://127.0.0.1:5000"])

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

processor = BlipProcessor.from_pretrained("Salesforce/blip-vqa-base")
model     = BlipForQuestionAnswering.from_pretrained("Salesforce/blip-vqa-base")
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

# Ask BLIP direct yes/no questions for each ADA feature
QUESTIONS = {
    "a staircase":  "are there stairs or steps in this image?",
    "a ramp":       "is there a wheelchair ramp or sloped ramp in this image?",
    "a wheelchair": "is there a wheelchair in this image?",
    "an elevator":  "is there an elevator or lift in this image?",
    "a curb cut":   "is there a curb cut or dropped curb on a sidewalk in this image?",
    "a handrail":   "is there a handrail or railing in this image?",
}

def ask(image: Image.Image, question: str) -> str:
    inputs = processor(image, question, return_tensors="pt")
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=10)
    return processor.decode(out[0], skip_special_tokens=True).strip().lower()

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

    result = {}
    for label, question in QUESTIONS.items():
        # Check full image first, then bottom half
        for img in [image, bottom_half]:
            answer = ask(img, question)
            print(f"Q: {question} | A: {answer}")
            if answer in ("yes", "yes."):
                result[label] = 1.0
                break  # found it, no need to check bottom half

    if not result:
        result["_caption"] = "no features detected"

    return jsonify(result)

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