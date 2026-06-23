from flask import Flask, request, jsonify, send_from_directory
from PIL import Image
from transformers import CLIPProcessor, CLIPModel
from supabase import create_client
import torch, io, os
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

@app.route("/geocode")
def geocode():
    import requests
    q = request.args.get('q', '')
    url = f"https://nominatim.openstreetmap.org/search?format=json&q={q}"
    res = requests.get(url, headers={"User-Agent": "PathAble/1.0"})
    if not res.text.strip():
        return jsonify([])
    return jsonify(res.json())

@app.route("/reverse")
def reverse():
    import requests
    lat = request.args.get('lat')
    lon = request.args.get('lon')
    url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}"
    res = requests.get(url, headers={"User-Agent": "PathAble/1.0"})
    if not res.text.strip():
        return jsonify({})
    return jsonify(res.json())

@app.route("/save-marker", methods=["POST"])
def save_marker():
    try:
        lat = float(request.form.get('lat'))
        lng = float(request.form.get('lng'))
        label = request.form.get('label')

        supabase.table("ada_markers").insert({
            "lat": lat,
            "lng": lng,
            "label": label,
            "image_url": None,
            "location": f"POINT({lng} {lat})"
        }).execute()

        return jsonify({"success": True})
    except Exception as e:
        print(f"ERROR in save_marker: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=False)