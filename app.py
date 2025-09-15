from flask import Flask, request, jsonify
from PyPDF2 import PdfReader
from pdf2image import convert_from_path
from PIL import Image
import fitz  # PyMuPDF
import os

app = Flask(__name__)

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

    # PyPDF2 analysis
    try:
        reader = PdfReader(file_path)
        result["fonts_enclosed"] = all([font.embedded for page in reader.pages for font in page.extract_fonts()]) if reader.pages else False
        result["layers"] = bool(reader.pages[0].get("/OCProperties")) if reader.pages else False
    except Exception as e:
        result["warnings"].append(f"PyPDF2 analysis failed: {str(e)}")

    # PyMuPDF analysis
    try:
        doc = fitz.open(file_path)
        for page in doc:
            for img in page.get_images(full=True):
                xref = img[0]
                pix = fitz.Pixmap(doc, xref)
                if pix.n < 4:  # RGB
                    result["content_color_modes"].append("RGB")
                else:  # CMYK or CMYK+alpha
                    result["content_color_modes"].append("CMYK")
                result["images_embedded"] += 1
                dpi = pix.xres
                if dpi < 150:
                    result["images_low_dpi"] += 1
                pix = None
        doc.close()
    except Exception as e:
        result["warnings"].append(f"PyMuPDF analysis failed: {str(e)}")

    # Determine document color mode
    if result["content_color_modes"]:
        if "CMYK" in result["content_color_modes"] and "RGB" in result["content_color_modes"]:
            result["mode_conflict"] = True
            result["document_color_mode"] = "Mixed"
        elif "CMYK" in result["content_color_modes"]:
            result["document_color_mode"] = "CMYK"
        elif "RGB" in result["content_color_modes"]:
            result["document_color_mode"] = "RGB"

    return result

@app.route("/analyze", methods=["POST"])
def analyze():
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400

    temp_path = f"/tmp/{file.filename}"
    file.save(temp_path)

    try:
        analysis_result = analyze_pdf(temp_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    return jsonify(analysis_result)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
