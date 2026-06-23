from flask import Flask, request, jsonify, send_from_directory
from PIL import Image
from transformers import CLIPProcessor, CLIPModel
from supabase import create_client
import torch, io, os
from flask_cors import CORS
import os

app = Flask(__name__)
CORS(app)

# Supabase setup - replace these with your actual values
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
    return jsonify(res.json())

@app.route("/reverse")
def reverse():
    import requests
    lat = request.args.get('lat')
    lon = request.args.get('lon')
    url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}"
    res = requests.get(url, headers={"User-Agent": "PathAble/1.0"})
    return jsonify(res.json())

@app.route("/save-marker", methods=["POST"])
@app.route("/save-marker", methods=["POST"])
def save_marker():
    import requests as req
    lat = float(request.form.get('lat'))
    lng = float(request.form.get('lng'))
    label = request.form.get('label')
    file = request.files.get('image')

    image_url = None
    if file:
        file_bytes = file.read()
        filename = f"{label.replace(' ', '_')}_{int(lat*1000)}_{int(lng*1000)}.jpg"
        storage_url = f"{SUPABASE_URL}/storage/v1/object/ada-images/{filename}"
        headers = {
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "image/jpeg"
        }
        r = req.post(storage_url, data=file_bytes, headers=headers)
        print(f"Storage upload status: {r.status_code}")
        print(f"Storage upload response: {r.text}")
        if r.status_code in [200, 201]:
            image_url = f"{SUPABASE_URL}/storage/v1/object/public/ada-images/{filename}"

    print(f"Saving marker: {label} at {lat},{lng} with image_url={image_url}")

    supabase.table("ada_markers").insert({
        "lat": lat,
        "lng": lng,
        "label": label,
        "image_url": image_url,
        "location": f"POINT({lng} {lat})"
    }).execute()

    return jsonify({"success": True})

if __name__ == "__main__":
    app.run(debug=False)