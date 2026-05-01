"""
KiranaLens — Remote Cash Flow Underwriting for Kirana Stores
Flask entry point
"""
import os, json, uuid
from flask import Flask, request, jsonify, render_template, redirect, url_for
from werkzeug.utils import secure_filename

from vision   import analyze_images
from geo      import get_geo_signals
from fraud    import run_fraud_checks
from scoring  import compute_kcs_score

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = "uploads"
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024   # 50 MB
ALLOWED = {"png", "jpg", "jpeg", "webp"}

def allowed(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED

# ── routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/assess", methods=["POST"])
def assess():
    """Main assessment endpoint.
    Accepts multipart form with:
      - images[]  : 3–5 store images
      - lat, lng  : GPS coordinates
      - shop_age  : (optional) years in operation
      - rent      : (optional) monthly rent in ₹
      - mock      : "1" to skip Ollama and use mock responses
    """
    use_mock = request.form.get("mock", "0") == "1"

    # ── 1. save uploaded images ──
    files = request.files.getlist("images[]")
    if not files or len(files) < 1:
        return jsonify({"error": "Upload at least 1 image"}), 400

    session_id = str(uuid.uuid4())[:8]
    session_dir = os.path.join(app.config["UPLOAD_FOLDER"], session_id)
    os.makedirs(session_dir, exist_ok=True)

    saved_paths = []
    for f in files:
        if f and allowed(f.filename):
            fname = secure_filename(f.filename)
            path  = os.path.join(session_dir, fname)
            f.save(path)
            saved_paths.append(path)

    if not saved_paths:
        return jsonify({"error": "No valid image files uploaded"}), 400

    # ── 2. collect inputs ──
    try:
        lat = float(request.form.get("lat", 0))
        lng = float(request.form.get("lng", 0))
    except ValueError:
        lat, lng = 0.0, 0.0

    optional = {
        "shop_age": request.form.get("shop_age"),
        "rent":     request.form.get("rent"),
    }

    # ── 3. run pipeline ──
    vision_signals = analyze_images(saved_paths, mock=use_mock)
    geo_signals    = get_geo_signals(lat, lng, mock=use_mock)
    fraud_result   = run_fraud_checks(saved_paths, vision_signals, geo_signals,
                                      lat, lng, mock=use_mock)
    result         = compute_kcs_score(vision_signals, geo_signals,
                                       fraud_result, optional)

    result["session_id"] = session_id
    result["images_used"] = len(saved_paths)

    # ── 4. persist result ──
    out_path = os.path.join(session_dir, "result.json")
    with open(out_path, "w") as fh:
        json.dump(result, fh, indent=2)

    return jsonify(result)

@app.route("/result/<session_id>")
def show_result(session_id):
    path = os.path.join(app.config["UPLOAD_FOLDER"], session_id, "result.json")
    if not os.path.exists(path):
        return "Session not found", 404
    with open(path) as fh:
        data = json.load(fh)
    return render_template("result.html", data=data)

@app.route("/health")
def health():
    return jsonify({"status": "ok", "version": "1.0.0"})

if __name__ == "__main__":
    os.makedirs("uploads", exist_ok=True)
    app.run(debug=True, port=5000)
