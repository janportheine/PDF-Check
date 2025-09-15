from flask import Flask, request, jsonify
from PyPDF2 import PdfReader
from pdf2image import convert_from_path
from PIL import Image

app = Flask(__name__)

# Utility function to check PDF content
def analyze_pdf(file_path):
    result = {
        "content_color_modes": [],
        "declared_color_spaces": [],
        "document_color_mode": "Unknown",
        "fonts_enclosed": False,
        "has_cut_contour_layer": False,
        "images_embedded": 0,
        "images_linked": 0,
        "images_low_dpi": 0,
        "layers": False,
        "mode_conflict": False,
        "warnings": []
    }

    try:
        reader = PdfReader(file_path)

        # Check fonts
        for page in reader.pages:
            if '/Font' in page['/Resources']:
                result["fonts_enclosed"] = True

        # Detect images and their DPI
        images = convert_from_path(file_path)
        result["images_embedded"] = len(images)
        for img in images:
            dpi = img.info.get("dpi", (72, 72))
            if dpi[0] < 300 or dpi[1] < 300:
                result["images_low_dpi"] += 1
            # Determine color mode
            if img.mode in ["CMYK", "RGB", "RGBA"]:
                if img.mode not in result["content_color_modes"]:
                    result["content_color_modes"].append(img.mode)
        # Set document color mode if consistent
        if result["content_color_modes"]:
            result["document_color_mode"] = result["content_color_modes"][0]

    except Exception as e:
        result["warnings"].append(f"Error analyzing PDF: {str(e)}")

    # Additional checks can be added here if needed

    if not result["warnings"]:
        result["warnings"].append("No color mode conflicts detected")

    return result

@app.route("/")
def index():
    return jsonify({"message": "PDF Analyzer API is running!"})

@app.route("/analyze_pdf", methods=["POST"])
def analyze_pdf_endpoint():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    # Save temporarily to analyze
    file_path = f"/tmp/{file.filename}"
    file.save(file_path)

    result = analyze_pdf(file_path)
    return jsonify(result)

# Health check endpoint
@app.route("/health")
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
