from flask import Flask, request, jsonify
from PIL import Image
from transformers import CLIPProcessor, CLIPModel
import torch
import io
from flask_cors import CORS

# Init model
model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")

# Flask app
app = Flask(__name__)
CORS(app)  # Allow cross-origin requests

@app.route("/analyze", methods=["POST"])
def analyze_image():
    file = request.files['image']
    image = Image.open(io.BytesIO(file.read())).convert("RGB")
    
    texts = ["a staircase", "a ramp", "a wheelchair", "an elevator", "no ADA feature"]
    inputs = processor(text=texts, images=image, return_tensors="pt", padding=True)
    outputs = model(**inputs)
    probs = outputs.logits_per_image.softmax(dim=1)[0].tolist()

    threshold = 0.10  # show labels with at least 10% confidence
    result = {
        label: round(prob, 4)
        for label, prob in zip(texts, probs)
        if prob >= threshold
    }

    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True)
